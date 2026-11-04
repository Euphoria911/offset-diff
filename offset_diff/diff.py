"""Diff two flattened offset maps."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from .parse import Offsets


@dataclass(frozen=True)
class Change:
    key: str
    old: int
    new: int

    @property
    def delta(self) -> int:
        return self.new - self.old


@dataclass(frozen=True)
class Rename:
    old_key: str
    new_key: str
    value: int


@dataclass
class DiffResult:
    added: Offsets = field(default_factory=dict)
    removed: Offsets = field(default_factory=dict)
    changed: list[Change] = field(default_factory=list)
    renamed: list[Rename] = field(default_factory=list)
    unchanged: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def _pattern(p: str) -> str:
    return p if any(c in p for c in "*?[") else f"*{p}*"


def select(offsets: Offsets, include: list[str], exclude: list[str]) -> Offsets:
    """Keep keys matching any ``include`` glob and no ``exclude`` glob (case-insensitive).

    A pattern without glob characters is treated as a substring match.
    """
    inc = [_pattern(p).lower() for p in include]
    exc = [_pattern(p).lower() for p in exclude]
    out: Offsets = {}
    for key, value in offsets.items():
        # match the full key or any dotted tail of it, so "m_i*" hits "client.dll.m_iHealth"
        low = key.lower()
        tails = [low] + [low[i + 1:] for i, ch in enumerate(low) if ch == "."]
        if inc and not any(fnmatchcase(t, p) for t in tails for p in inc):
            continue
        if any(fnmatchcase(t, p) for t in tails for p in exc):
            continue
        out[key] = value
    return out


def _detect_renames(added: Offsets, removed: Offsets) -> list[Rename]:
    """Pair a removed key with an added key when they share a unique non-zero value."""
    added_counts = Counter(added.values())
    removed_counts = Counter(removed.values())
    by_value = {v: k for k, v in added.items()}
    renames = []
    for old_key, value in removed.items():
        if value and added_counts[value] == 1 and removed_counts[value] == 1:
            renames.append(Rename(old_key, by_value[value], value))
    return sorted(renames, key=lambda r: r.old_key)


def diff(old: Offsets, new: Offsets, min_delta: int = 0) -> DiffResult:
    """Compare ``old`` and ``new``.

    ``min_delta`` hides changes whose absolute delta is smaller (they count as unchanged).
    """
    result = DiffResult()
    for key in sorted(old.keys() | new.keys()):
        in_old, in_new = key in old, key in new
        if in_old and in_new:
            if old[key] == new[key] or abs(new[key] - old[key]) < min_delta:
                result.unchanged += 1
            else:
                result.changed.append(Change(key, old[key], new[key]))
        elif in_new:
            result.added[key] = new[key]
        else:
            result.removed[key] = old[key]

    result.renamed = _detect_renames(result.added, result.removed)
    return result
