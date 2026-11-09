"""Render a DiffResult as text, markdown, JSON, or a standalone HTML page."""

from __future__ import annotations

import html
import json

from .diff import DiffResult, Rename, Shift


def _hex(value: int) -> str:
    return f"0x{value:X}"


def _signed(value: int) -> str:
    return f"{'+' if value >= 0 else '-'}0x{abs(value):X}"


def _rename_detail(rn: Rename) -> str:
    if rn.reason == "position":
        where = f"{_hex(rn.old_value)} -> {_hex(rn.new_value)}, same position"
    else:
        where = f"{_hex(rn.new_value)}, same value"
    return f"{where}, name {rn.similarity:.0%}"


def _shift_span(sh: Shift) -> str:
    return f"{sh.first} .. {sh.last}"


def _shift_detail(sh: Shift) -> str:
    return f"{sh.count} fields, from {_hex(sh.first_old)} -> {_hex(sh.first_new)}"


def _summary(r: DiffResult) -> str:
    return (
        f"{len(r.changed)} changed, {len(r.added)} added, {len(r.removed)} removed, "
        f"{len(r.renamed)} renamed?, {r.unchanged} unchanged"
    )


def render_text(r: DiffResult, old_label: str, new_label: str, group: bool = False) -> str:
    lines = [f"--- {old_label}", f"+++ {new_label}", _summary(r), ""]

    if group and r.shifts:
        width = max(len(sh.scope) for sh in r.shifts)
        lines.append("LAYOUT SHIFTS (neighbouring fields that moved by the same amount)")
        for sh in r.shifts:
            lines.append(
                f"  {sh.scope:<{width}}  {_signed(sh.delta)}  {_shift_span(sh)}  ({_shift_detail(sh)})"
            )
        lines.append("")

    if r.changed:
        width = max(len(c.key) for c in r.changed)
        lines.append("CHANGED")
        for c in r.changed:
            lines.append(
                f"  {c.key:<{width}}  {_hex(c.old)} -> {_hex(c.new)}  ({_signed(c.delta)})"
            )
        lines.append("")
    if r.renamed:
        lines.append("POSSIBLE RENAMES (ranked by name similarity)")
        for rn in r.renamed:
            lines.append(f"  {rn.old_key} -> {rn.new_key}  ({_rename_detail(rn)})")
        lines.append("")
    renamed_old = {rn.old_key for rn in r.renamed}
    renamed_new = {rn.new_key for rn in r.renamed}
    added = {k: v for k, v in r.added.items() if k not in renamed_new}
    removed = {k: v for k, v in r.removed.items() if k not in renamed_old}
    if added:
        lines.append("ADDED")
        lines.extend(f"  + {k} = {_hex(v)}" for k, v in added.items())
        lines.append("")
    if removed:
        lines.append("REMOVED")
        lines.extend(f"  - {k} = {_hex(v)}" for k, v in removed.items())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(r: DiffResult, old_label: str, new_label: str, group: bool = False) -> str:
    out = [f"# Offset diff", "", f"`{old_label}` -> `{new_label}`", "", _summary(r), ""]
    if group and r.shifts:
        out += ["## Layout shifts", "", "| Scope | Shift | Fields | From |", "| --- | --- | --- | --- |"]
        out += [
            f"| `{sh.scope}` | `{_signed(sh.delta)}` | `{_shift_span(sh)}` ({sh.count}) "
            f"| `{_hex(sh.first_old)}` -> `{_hex(sh.first_new)}` |"
            for sh in r.shifts
        ]
        out.append("")
    if r.changed:
        out += ["## Changed", "", "| Key | Old | New | Delta |", "| --- | --- | --- | --- |"]
        out += [
            f"| `{c.key}` | `{_hex(c.old)}` | `{_hex(c.new)}` | `{_signed(c.delta)}` |"
            for c in r.changed
        ]
        out.append("")
    if r.renamed:
        out += ["## Possible renames", ""]
        out += [f"- `{rn.old_key}` -> `{rn.new_key}` ({_rename_detail(rn)})" for rn in r.renamed]
        out.append("")
    if r.added:
        out += ["## Added", ""] + [f"- `{k}` = `{_hex(v)}`" for k, v in r.added.items()] + [""]
    if r.removed:
        out += ["## Removed", ""] + [f"- `{k}` = `{_hex(v)}`" for k, v in r.removed.items()] + [""]
    return "\n".join(out).rstrip() + "\n"


def render_json(r: DiffResult, old_label: str, new_label: str, group: bool = False) -> str:
    payload = {
        "old": old_label,
        "new": new_label,
        "changed": [
            {"key": c.key, "old": c.old, "new": c.new, "delta": c.delta} for c in r.changed
        ],
        "added": r.added,
        "removed": r.removed,
        "renamed": [
            {
                "old": rn.old_key,
                "new": rn.new_key,
                "value": rn.new_value,
                "old_value": rn.old_value,
                "new_value": rn.new_value,
                "reason": rn.reason,
                "similarity": round(rn.similarity, 3),
            }
            for rn in r.renamed
        ],
        "unchanged": r.unchanged,
    }
    if group:
        payload["shifts"] = [
            {
                "scope": sh.scope,
                "delta": sh.delta,
                "first": sh.first,
                "last": sh.last,
                "count": sh.count,
                "first_old": sh.first_old,
                "first_new": sh.first_new,
            }
            for sh in r.shifts
        ]
    return json.dumps(payload, indent=2) + "\n"


_HTML_HEAD = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Offset diff</title>
<style>
:root { --bg:#fff; --fg:#1b1f24; --muted:#5b6570; --line:#d8dee4; --card:#f6f8fa;
        --up:#9a6700; --add:#1a7f37; --del:#cf222e; --ren:#0969da; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e; --line:#30363d; --card:#161b22;
          --up:#d29922; --add:#3fb950; --del:#f85149; --ren:#58a6ff; }
}
body { margin:0 auto; max-width:1100px; padding:24px 16px 64px; background:var(--bg); color:var(--fg);
       font:14px/1.5 system-ui, sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:15px; margin:28px 0 8px; }
code, td.k, td.v { font-family:ui-monospace, Consolas, monospace; font-size:13px; }
.labels { color:var(--muted); margin:0 0 16px; }
.chips { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 16px; }
.chip { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:4px 10px; }
.chip b { margin-right:4px; }
input { width:100%; box-sizing:border-box; padding:8px 10px; margin:0 0 8px; color:var(--fg);
        background:var(--card); border:1px solid var(--line); border-radius:6px; font:inherit; }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:4px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; }
td.k { word-break:break-all; }
td.v, th.v { text-align:right; white-space:nowrap; }
.up { color:var(--up); } .add { color:var(--add); } .del { color:var(--del); } .ren { color:var(--ren); }
.hint { color:var(--muted); }
tr.hide { display:none; }
</style>
</head>
<body>
"""

_HTML_TAIL = """\
<script>
(function () {
  var box = document.getElementById("q");
  box.addEventListener("input", function () {
    var q = box.value.toLowerCase();
    document.querySelectorAll("tbody tr").forEach(function (row) {
      row.classList.toggle("hide", q !== "" && row.textContent.toLowerCase().indexOf(q) < 0);
    });
  });
})();
</script>
</body>
</html>
"""


def _table(title: str, head: list[tuple[str, str]], rows: list[list[tuple[str, str]]]) -> list[str]:
    """One section: ``head`` is (label, css class) per column, ``rows`` is (cell html, css class)."""
    out = [f"<h2>{html.escape(title)} ({len(rows)})</h2>", "<table>", "<thead><tr>"]
    out += [f'<th class="{cls}">{html.escape(label)}</th>' for label, cls in head]
    out += ["</tr></thead>", "<tbody>"]
    for row in rows:
        out.append("<tr>" + "".join(f'<td class="{cls}">{cell}</td>' for cell, cls in row) + "</tr>")
    out += ["</tbody>", "</table>"]
    return out


def render_html(r: DiffResult, old_label: str, new_label: str, group: bool = False) -> str:
    """Self-contained page: no scripts or styles fetched, a live filter box over every row."""
    esc = html.escape
    out = [
        _HTML_HEAD,
        "<h1>Offset diff</h1>",
        f'<p class="labels"><code>{esc(old_label)}</code> &rarr; <code>{esc(new_label)}</code></p>',
        '<div class="chips">',
    ]
    for count, name, cls in (
        (len(r.changed), "changed", "up"),
        (len(r.added), "added", "add"),
        (len(r.removed), "removed", "del"),
        (len(r.renamed), "renamed?", "ren"),
        (r.unchanged, "unchanged", "hint"),
    ):
        out.append(f'<span class="chip"><b class="{cls}">{count}</b>{name}</span>')
    out += ["</div>", '<input id="q" type="search" placeholder="filter keys" autocomplete="off">']

    if group and r.shifts:
        out += _table(
            "Layout shifts",
            [("Scope", "k"), ("Shift", "v"), ("Fields", "k"), ("From", "v")],
            [
                [
                    (esc(sh.scope), "k"),
                    (_signed(sh.delta), "v up"),
                    (esc(f"{_shift_span(sh)} ({sh.count})"), "k"),
                    (f"{_hex(sh.first_old)} &rarr; {_hex(sh.first_new)}", "v"),
                ]
                for sh in r.shifts
            ],
        )

    if r.changed:
        out += _table(
            "Changed",
            [("Key", "k"), ("Old", "v"), ("New", "v"), ("Delta", "v")],
            [
                [
                    (esc(c.key), "k"),
                    (_hex(c.old), "v"),
                    (_hex(c.new), "v"),
                    (_signed(c.delta), "v up"),
                ]
                for c in r.changed
            ],
        )
    if r.renamed:
        out += _table(
            "Possible renames",
            [("Old", "k"), ("New", "k"), ("Evidence", "")],
            [
                [
                    (esc(rn.old_key), "k del"),
                    (esc(rn.new_key), "k add"),
                    (esc(_rename_detail(rn)), "hint"),
                ]
                for rn in r.renamed
            ],
        )
    renamed_old = {rn.old_key for rn in r.renamed}
    renamed_new = {rn.new_key for rn in r.renamed}
    added = [(k, v) for k, v in r.added.items() if k not in renamed_new]
    removed = [(k, v) for k, v in r.removed.items() if k not in renamed_old]
    if added:
        out += _table(
            "Added", [("Key", "k"), ("Value", "v")], [[(esc(k), "k add"), (_hex(v), "v")] for k, v in added]
        )
    if removed:
        out += _table(
            "Removed",
            [("Key", "k"), ("Value", "v")],
            [[(esc(k), "k del"), (_hex(v), "v")] for k, v in removed],
        )
    if not r.has_changes:
        out.append('<p class="hint">No differences.</p>')
    out.append(_HTML_TAIL)
    return "\n".join(out)


RENDERERS = {"text": render_text, "md": render_markdown, "json": render_json, "html": render_html}
