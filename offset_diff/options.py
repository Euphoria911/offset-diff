"""Command-line options shared by the diff command and ``watch``."""

from __future__ import annotations

import argparse


def add_selection_options(p: argparse.ArgumentParser) -> None:
    """Which keys take part in a diff: ``-i``, ``-x``, ``--only``, ``--min-delta``."""
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
