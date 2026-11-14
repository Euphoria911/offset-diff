from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .diff import diff, select
from .gitsource import load_rev
from .options import add_selection_options
from .parse import load
from .report import RENDERERS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="offset-diff",
        description="Diff offset dumps (cs2-dumper, a2x, Dumper-7 style) across game builds.",
    )
    p.add_argument("old", help="old dump: .json / .hpp / .cs / .rs file, or dumper output dir")
    p.add_argument("new", help="new dump, same formats")
    p.add_argument(
        "--repo", metavar="DIR",
        help="read OLD and NEW from this git repo as REV:PATH (e.g. HEAD~1:output/offsets.json)",
    )
    p.add_argument(
        "-p", "--path", metavar="PATH",
        help="with --repo: file or folder to read when OLD / NEW are bare revisions",
    )
    p.add_argument("-f", "--format", choices=sorted(RENDERERS), default="text")
    p.add_argument("-o", "--output", help="write report to this file instead of stdout")
    add_selection_options(p)
    p.add_argument(
        "--fail-on-change", action="store_true",
        help="exit 1 when the dumps differ (for CI / patch-day watchers)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "watch":
        from .watch import watch_main

        return watch_main(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.path and not args.repo:
        parser.error("--path needs --repo")
    if args.repo:
        read = lambda spec: load_rev(args.repo, spec, args.path)
    else:
        read = load
    try:
        old = select(read(args.old), args.include, args.exclude, args.only)
        new = select(read(args.new), args.include, args.exclude, args.only)
    except (OSError, ValueError) as exc:
        print(f"offset-diff: {exc}", file=sys.stderr)
        return 2

    result = diff(old, new, min_delta=args.min_delta)
    report = RENDERERS[args.format](result, str(args.old), str(args.new), group=args.group)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(report)

    return 1 if args.fail_on_change and result.has_changes else 0
