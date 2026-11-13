"""Parsers that flatten an offset dump into ``{dotted.key: int}``.

Supported inputs:
  * JSON  - any nesting; ints and "0x..." strings are kept, the rest ignored.
  * C++ / Rust / C# source - ``constexpr``/``const`` integer declarations,
    qualified by the enclosing namespace / mod / class / struct names.
  * Il2CppDumper ``dump.cs`` - field offsets and method RVAs (detected by its
    ``// TypeDefIndex:`` markers, see il2cpp.py).
  * A directory of dumper output (one entry per file stem, json preferred).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from .il2cpp import MARKER as _IL2CPP_MARKER
from .il2cpp import parse_il2cpp

Offsets = dict[str, int]

_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_HEX_STR_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")

# one token per match: a container opener, a bare brace, or a const declaration
_TOKEN_RE = re.compile(
    r"""
    (?P<container>\b(?:namespace|mod|class|struct)\s+(?P<cname>[A-Za-z_][\w.]*)[^{;=]*\{)
    | (?P<open>\{)
    | (?P<close>\})
    | (?P<decl>\b(?:constexpr|const)\b[^=;{}]*?\b(?P<name>[A-Za-z_]\w*)\s*
        (?::\s*[\w:]+\s*)?=\s*(?P<value>0[xX][0-9a-fA-F]+|\d+)[uUlL]*\s*;)
    """,
    re.VERBOSE,
)

SOURCE_SUFFIXES = {".hpp", ".h", ".cs", ".rs", ".txt"}
_DIR_PREFERENCE = (".json", ".hpp", ".h", ".cs", ".rs")


def _flatten(node: object, prefix: str, out: Offsets) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, int):
        out[prefix] = node
    elif isinstance(node, str) and _HEX_STR_RE.match(node):
        out[prefix] = int(node, 16)
    elif isinstance(node, dict):
        for key, value in node.items():
            _flatten(value, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _flatten(value, f"{prefix}.{index}" if prefix else str(index), out)


def parse_json(text: str) -> Offsets:
    out: Offsets = {}
    _flatten(json.loads(text), "", out)
    return out


def parse_source(text: str) -> Offsets:
    text = _COMMENT_RE.sub("", text)
    out: Offsets = {}
    stack: list[str | None] = []
    for m in _TOKEN_RE.finditer(text):
        if m.group("container"):
            stack.append(m.group("cname"))
        elif m.group("open"):
            stack.append(None)
        elif m.group("close"):
            if stack:
                stack.pop()
        else:
            scope = ".".join(s for s in stack if s)
            name = m.group("name")
            out[f"{scope}.{name}" if scope else name] = int(m.group("value"), 0)
    return out


def parse_text(text: str, hint: str = "") -> Offsets:
    """Parse ``text``; ``hint`` is a file suffix like ``.json`` or ``.hpp``."""
    hint = hint.lower()
    if hint == ".json" or (hint not in SOURCE_SUFFIXES and text.lstrip()[:1] in "{["):
        return parse_json(text)
    if _IL2CPP_MARKER in text:
        return parse_il2cpp(text)
    return parse_source(text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def load(path: str | Path) -> Offsets:
    """Load one dump file, or a dumper output directory."""
    path = Path(path)
    if path.is_dir():
        return _load_dir(path)
    return parse_text(_read(path), path.suffix)


def parse_named(names: Iterable[str], read: Callable[[str], str], where: str) -> Offsets:
    """Merge a folder of dumper output: one entry per file stem, JSON preferred over source.

    ``names`` are the file names in the folder and ``read(name)`` returns one file's text;
    only the preferred variant of each stem is read. ``where`` names the folder in errors.
    """
    by_stem: dict[str, dict[str, str]] = {}
    for name in sorted(names):
        stem, suffix = Path(name).stem, Path(name).suffix.lower()
        if suffix in _DIR_PREFERENCE:
            by_stem.setdefault(stem, {})[suffix] = name

    out: Offsets = {}
    for stem, variants in sorted(by_stem.items()):
        chosen = next(variants[s] for s in _DIR_PREFERENCE if s in variants)
        for key, value in parse_text(read(chosen), Path(chosen).suffix).items():
            out[f"{stem}.{key}"] = value
    if not out:
        raise ValueError(f"no parseable offset files in {where}")
    return out


def _load_dir(directory: Path) -> Offsets:
    names = [child.name for child in directory.iterdir() if child.is_file()]
    return parse_named(names, lambda name: _read(directory / name), str(directory))
