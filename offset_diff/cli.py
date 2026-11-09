from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .diff import diff, select
from .parse import load
from .report import RENDERERS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="offset-diff",
        description="Diff offset dumps (cs2-dumper, a2x, Dumper-7 style) across game builds.",
    )
    p.add_argument("old", help="old dump: .json / .hpp / .cs / .rs file, or dumper output dir")
    p.add_argument("new", help="new dump, same formats")
    p.add_argument("-f", "--format", choices=sorted(RENDERERS), default="text")
    p.add_argument("-o", "--output", help="write report to this file instead of stdout")
    p.add_argument(
        "-i", "--include", action="append", default=[], metavar="PAT",
        help="only keys matching PAT (glob, or substring if no wildcard); repeatable",
    )
    p.add_argument(
        "-x", "--exclude", action="append", default=[], metavar="PAT",
        help="drop keys matching PAT; repeatable",
    )
    p.add_argument(
        "--only", choices=("fields", "methods"),
        help="il2cpp dumps: compare only field offsets or only method RVAs",
    )
    p.add_argument(
        "--min-delta", type=lambda s: int(s, 0), default=0, metavar="N",
        help="ignore changes smaller than N bytes (accepts 0x..)",
    )
    p.add_argument(
        "-g", "--group", action="store_true",
        help="add a layout-shift summary: runs of neighbouring fields that moved by the same amount",
    )
    p.add_argument(
        "--fail-on-change", action="store_true",
        help="exit 1 when the dumps differ (for CI / patch-day watchers)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        old = select(load(args.old), args.include, args.exclude, args.only)
        new = select(load(args.new), args.include, args.exclude, args.only)
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
