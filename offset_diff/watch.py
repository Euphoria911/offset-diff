"""``offset-diff watch``: poll a dumper repo and diff every new commit that changed the dump.

Nothing is checked out and the working tree is never touched: the repo is ``git fetch``ed,
then each new commit's dump is read straight from the object store and compared with the
dump as it was after the previous commit. A commit that touched the file but moved no
offset (a timestamp, a comment) prints nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .diff import DiffResult, diff, select
from .gitsource import commits_between, load_rev, path_exists, resolve, run_git, subject
from .options import add_selection_options
from .parse import Offsets
from .report import RENDERERS

FORMATS = ("text", "md", "json")
_EXT = {"text": "txt", "md": "md", "json": "json"}


@dataclass(frozen=True)
class Update:
    commit: str  # full hash of the commit that changed offsets
    subject: str
    base: str  # hash of the commit it was compared against
    result: DiffResult


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="offset-diff watch",
        description="Poll a dumper repo and diff every new commit that changed the dump.",
    )
    p.add_argument("--repo", required=True, metavar="DIR", help="local clone of the dumper repo")
    p.add_argument("-p", "--path", required=True, metavar="PATH",
                   help="file or folder inside the repo to watch, e.g. output/offsets.json")
    p.add_argument("--ref", default="@{u}", metavar="REF",
                   help="what to follow (default: the upstream of the checked-out branch)")
    p.add_argument("--state", metavar="FILE",
                   help="remember the last commit seen here, so a restart or a cron run resumes")
    p.add_argument("--since", metavar="REV",
                   help="first run only: report everything after REV (default: start from now)")
    p.add_argument("--interval", type=float, default=300.0, metavar="SEC",
                   help="seconds between checks (default 300)")
    p.add_argument("--once", action="store_true",
                   help="check once and exit; status 1 if offsets changed, for cron / CI")
    p.add_argument("--no-fetch", action="store_true", help="don't run git fetch before each check")
    p.add_argument("-f", "--format", choices=FORMATS, default="text")
    p.add_argument("--out-dir", metavar="DIR", help="also write each report to DIR/<commit>.<ext>")
    add_selection_options(p)
    return p


def _snapshot(repo: str, rev: str, path: str, include, exclude, only) -> Offsets:
    if not path_exists(repo, rev, path):
        return {}  # the file is new (or gone) at this commit
    return select(load_rev(repo, f"{rev}:{path}"), list(include), list(exclude), only)


def poll(
    repo: str,
    ref: str,
    path: str,
    last: str,
    include=(),
    exclude=(),
    only: str | None = None,
    min_delta: int = 0,
) -> tuple[str, list[Update], list[str]]:
    """Compare every commit in ``last..ref`` that touched ``path`` with the one before it.

    Returns the new head, the commits that moved offsets (oldest first) and any warnings.
    A commit whose dump fails to parse is skipped, and the next one is compared with the
    last good state.
    """
    head = resolve(repo, ref)
    warnings: list[str] = []
    if head == last:
        return head, [], warnings
    try:
        resolve(repo, last)
    except ValueError:
        warnings.append(f"last seen commit {last[:8]} no longer exists; restarting from {head[:8]}")
        return head, [], warnings

    try:
        prev = _snapshot(repo, last, path, include, exclude, only)
    except ValueError as exc:
        warnings.append(f"{last[:8]}: {exc}; treating it as empty")
        prev = {}
    prev_sha = last
    updates: list[Update] = []
    for commit in commits_between(repo, last, head, path):
        try:
            data = _snapshot(repo, commit, path, include, exclude, only)
        except ValueError as exc:
            warnings.append(f"{commit[:8]}: skipped, {exc}")
            continue
        result = diff(prev, data, min_delta=min_delta)
        if result.has_changes:
            updates.append(Update(commit, subject(repo, commit), prev_sha, result))
        prev, prev_sha = data, commit
    return head, updates, warnings


def format_update(u: Update, path: str, fmt: str, group: bool = False) -> str:
    report = RENDERERS[fmt](u.result, f"{u.base[:8]}:{path}", f"{u.commit[:8]}:{path}", group=group)
    if fmt == "json":  # one JSON object per line
        data = json.loads(report)
        data["commit"], data["subject"] = u.commit, u.subject
        return json.dumps(data) + "\n"
    if fmt == "md":
        return f"## Commit `{u.commit[:8]}` {u.subject}\n\n{report}\n"
    return f"commit {u.commit[:8]}  {u.subject}\n{report}\n"


def _read_state(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def _write_state(path: str | None, sha: str) -> None:
    if path:
        tmp = f"{path}.tmp"
        Path(tmp).write_text(sha + "\n", encoding="utf-8")
        os.replace(tmp, path)  # a crash mid-write must not leave a half-written state


def _fetch(repo: str) -> str | None:
    try:
        run_git(repo, "fetch", "--quiet")
    except ValueError as exc:
        return f"fetch failed ({exc}); using what is already local"
    return None


def run(args: argparse.Namespace, sleep: Callable[[float], None] = time.sleep) -> int:
    path = args.path.strip("/")
    err = lambda msg: print(f"offset-diff watch: {msg}", file=sys.stderr, flush=True)
    try:
        head = resolve(args.repo, args.ref)
        last = _read_state(args.state)
        if last is None and args.since:
            last = resolve(args.repo, args.since)
        if last is None:
            _write_state(args.state, head)
            err(f"watching {args.ref} at {head[:8]} ({path}); new commits will be reported")
            if args.once:
                return 0
            last = head
    except (ValueError, OSError) as exc:
        err(str(exc))
        return 2

    if args.out_dir:
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    while True:
        if not args.no_fetch:
            warning = _fetch(args.repo)
            if warning:
                err(warning)
        try:
            head, updates, warnings = poll(
                args.repo, args.ref, path, last,
                args.include, args.exclude, args.only, args.min_delta,
            )
        except ValueError as exc:
            err(str(exc))
            if args.once:
                return 2
            updates, warnings = [], []  # transient; try again next round
        else:
            for w in warnings:
                err(w)
            for u in updates:
                text = format_update(u, path, args.format, args.group)
                sys.stdout.write(text)
                sys.stdout.flush()
                if args.out_dir:
                    name = f"{u.commit[:12]}.{_EXT[args.format]}"
                    Path(args.out_dir, name).write_text(text, encoding="utf-8")
            last = head
            _write_state(args.state, last)
        if args.once:
            return 1 if updates else 0
        sleep(args.interval)


def watch_main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return run(args)
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130
