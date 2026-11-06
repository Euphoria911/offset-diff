"""Render a DiffResult as text, markdown, or JSON."""

from __future__ import annotations

import json

from .diff import DiffResult, Rename


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


def _summary(r: DiffResult) -> str:
    return (
        f"{len(r.changed)} changed, {len(r.added)} added, {len(r.removed)} removed, "
        f"{len(r.renamed)} renamed?, {r.unchanged} unchanged"
    )


def render_text(r: DiffResult, old_label: str, new_label: str) -> str:
    lines = [f"--- {old_label}", f"+++ {new_label}", _summary(r), ""]

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


def render_markdown(r: DiffResult, old_label: str, new_label: str) -> str:
    out = [f"# Offset diff", "", f"`{old_label}` -> `{new_label}`", "", _summary(r), ""]
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


def render_json(r: DiffResult, old_label: str, new_label: str) -> str:
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
    return json.dumps(payload, indent=2) + "\n"


RENDERERS = {"text": render_text, "md": render_markdown, "json": render_json}
