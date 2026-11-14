"""Load a dump straight out of git history, so two builds of one repo can be diffed.

Specs use git's own ``REV:PATH`` syntax: ``HEAD~1:output/offsets.json``, ``v1.2:output`` (a
folder of dumper output), ``a1b2c3d:dump.cs``. Nothing is checked out; the blobs are read
with ``git cat-file`` and ``git ls-tree``.
"""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath

from .parse import Offsets, parse_named, parse_text


def _git(repo: str, *args: str) -> bytes:
    try:
        proc = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    except FileNotFoundError:
        raise ValueError("git was not found on PATH") from None
    except OSError as exc:
        raise ValueError(f"cannot run git: {exc}") from None
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ValueError(detail[-1] if detail else f"git {args[0]} failed")
    return proc.stdout


def run_git(repo: str, *args: str) -> bytes:
    """Run ``git -C repo ARGS`` and return stdout; any failure becomes a ``ValueError``."""
    return _git(repo, *args)


def resolve(repo: str, rev: str) -> str:
    """Full hash of the commit ``rev`` names (``@{u}``, ``origin/main``, a tag, ...)."""
    if not rev or rev.startswith("-"):
        raise ValueError(f"invalid revision {rev!r}")
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").decode().strip()
    except ValueError:
        raise ValueError(f"cannot resolve revision {rev!r}") from None


def path_exists(repo: str, rev: str, path: str) -> bool:
    try:
        _git(repo, "cat-file", "-e", f"{rev}:{path.strip('/')}")
    except ValueError:
        return False
    return True


def commits_between(repo: str, old: str, new: str, path: str = "") -> list[str]:
    """Commits in ``old..new`` that touch ``path``, oldest first."""
    args = ["rev-list", "--reverse", f"{old}..{new}"]
    if path:
        args += ["--", path.strip("/")]
    return _git(repo, *args).decode().split()


def subject(repo: str, commit: str) -> str:
    return _git(repo, "log", "-1", "--format=%s", commit).decode("utf-8", "replace").strip()


def _decode(blob: bytes) -> str:
    return blob.decode("utf-8-sig", errors="replace")


def split_spec(spec: str, default_path: str | None = None) -> tuple[str, str]:
    """``REV:PATH`` -> (rev, path). A bare ``REV`` takes ``default_path``."""
    rev, sep, path = spec.partition(":")
    if not sep:
        if default_path is None:
            raise ValueError(f"expected REV:PATH, got {spec!r} (or pass --path)")
        path = default_path
    if not rev or rev.startswith("-"):
        raise ValueError(f"invalid revision in {spec!r}")
    return rev, path.strip("/")


def load_rev(repo: str, spec: str, default_path: str | None = None) -> Offsets:
    """Parse the file or folder that ``spec`` names inside ``repo``."""
    rev, path = split_spec(spec, default_path)
    obj = f"{rev}:{path}"
    kind = _git(repo, "cat-file", "-t", obj).strip()

    if kind == b"blob":
        return parse_text(_decode(_git(repo, "cat-file", "blob", obj)), PurePosixPath(path).suffix)
    if kind == b"tree":
        entries = _git(repo, "ls-tree", "-z", obj).split(b"\0")
        names = []
        for entry in entries:
            meta, _, name = entry.partition(b"\t")
            if meta.split(b" ")[1:2] == [b"blob"]:
                names.append(name.decode("utf-8", "replace"))
        prefix = f"{obj}/" if path else f"{rev}:"
        return parse_named(names, lambda n: _decode(_git(repo, "cat-file", "blob", prefix + n)), obj)
    raise ValueError(f"{obj} is a {kind.decode()}, not a file or folder")
