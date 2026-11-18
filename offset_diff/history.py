"""``offset-diff history``: how offsets moved over the commits of a dumper repo.

Walks the commits that touched one file or folder, newest first like ``git log``, and for each
one prints what changed compared with the commit before it. Narrow it with ``-i`` to follow a
single offset (``-i dwViewMatrix``) and see exactly when it moved and by how much. Like
``watch``, it reads from git's object store and never touches the working tree.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from .diff import DiffResult, diff
from .gitsource import commit_date, recent_commits, resolve, subject
from .options import add_selection_options
from .report import _hex, _signed
from .watch import snapshot

FORMATS = ("text", "json")


@dataclass(frozen=True)
class Entry:
    commit: str
    date: str
    subject: str
    base: str | None  # the commit it was compared with; None for the first commit ever
    result: DiffResult


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="offset-diff history",
        description="Show how offsets changed over the commits of a dumper repo.",
    )
    p.add_argument("--repo", required=True, metavar="DIR", help="local clone of the dumper repo")
    p.add_argument("-p", "--path", required=True, metavar="PATH",
                   help="file or folder inside the repo, e.g. output/offsets.json")
    p.add_argument("--ref", default="HEAD", metavar="REF", help="where to start (default HEAD)")
    p.add_argument("-n", "--limit", type=int, default=20, metavar="N",
                   help="look at the last N commits that touched PATH (default 20, 0 = all)")
    p.add_argument("-f", "--format", choices=FORMATS, default="text")
    add_selection_options(p, group=False)
    return p


def collect(
    repo: str,
    ref: str,
    path: str,
    limit: int = 20,
    include=(),
    exclude=(),
    only: str | None = None,
    min_delta: int = 0,
) -> tuple[list[Entry], list[str]]:
    """The commits (newest first) among the last ``limit`` that touched ``path`` and changed a
    selected offset, plus warnings. With the whole history in range, the first commit shows
    everything it introduced; otherwise the oldest commit in range is only the baseline."""
    head = resolve(repo, ref)
    newest_first = recent_commits(repo, head, path, limit + 1 if limit > 0 else 0)
    chronological = list(reversed(newest_first))
    warnings: list[str] = []

    prev: dict[str, int] = {}
    prev_sha: str | None = None
    whole_history = limit <= 0 or len(newest_first) <= limit
    if chronological and not whole_history:
        baseline, chronological = chronological[0], chronological[1:]
        try:
            prev = snapshot(repo, baseline, path, include, exclude, only)
        except ValueError as exc:
            warnings.append(f"{baseline[:8]}: {exc}; comparing the next commit against nothing")
        prev_sha = baseline

    entries: list[Entry] = []
    for commit in chronological:
        try:
            data = snapshot(repo, commit, path, include, exclude, only)
        except ValueError as exc:
            warnings.append(f"{commit[:8]}: skipped, {exc}")
            continue
        result = diff(prev, data, min_delta=min_delta)
        if result.has_changes:
            entries.append(Entry(commit, commit_date(repo, commit), subject(repo, commit), prev_sha, result))
        prev, prev_sha = data, commit
    entries.reverse()
    return entries, warnings


def format_entry(entry: Entry, fmt: str = "text") -> str:
    r = entry.result
    if fmt == "json":
        return json.dumps({
            "commit": entry.commit,
            "date": entry.date,
            "subject": entry.subject,
            "base": entry.base,
            "changed": [{"key": c.key, "old": c.old, "new": c.new, "delta": c.delta} for c in r.changed],
            "added": r.added,
            "removed": r.removed,
            "renamed": [{"old": x.old_key, "new": x.new_key, "reason": x.reason} for x in r.renamed],
        }) + "\n"

    lines = [f"{entry.commit[:8]}  {entry.date}  {entry.subject}"]
    renamed_old = {x.old_key for x in r.renamed}
    renamed_new = {x.new_key for x in r.renamed}
    width = max((len(k) for k in [c.key for c in r.changed] + list(r.added) + list(r.removed)), default=0)
    for c in r.changed:
        lines.append(f"    ~ {c.key:<{width}}  {_hex(c.old)} -> {_hex(c.new)}  ({_signed(c.delta)})")
    for x in r.renamed:
        lines.append(f"    ? {x.old_key} -> {x.new_key}  (renamed? {x.reason})")
    for key, value in r.added.items():
        if key not in renamed_new:
            lines.append(f"    + {key:<{width}}  {_hex(value)}")
    for key, value in r.removed.items():
        if key not in renamed_old:
            lines.append(f"    - {key:<{width}}  {_hex(value)}")
    return "\n".join(lines) + "\n"


def history_main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 0:
        print("offset-diff history: --limit must be 0 or more", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        entries, warnings = collect(
            args.repo, args.ref, args.path.strip("/"), args.limit,
            args.include, args.exclude, args.only, args.min_delta,
        )
    except ValueError as exc:
        print(f"offset-diff history: {exc}", file=sys.stderr)
        return 2
    for w in warnings:
        print(f"offset-diff history: {w}", file=sys.stderr)
    for i, entry in enumerate(entries):
        if i and args.format == "text":
            sys.stdout.write("\n")
        sys.stdout.write(format_entry(entry, args.format))
    if not entries and args.format == "text":
        print("no commit in range changed the selected offsets", file=sys.stderr)
    return 0
