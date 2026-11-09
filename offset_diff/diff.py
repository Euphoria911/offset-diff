"""Diff two flattened offset maps."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fnmatch import fnmatchcase
from itertools import groupby

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
    old_value: int
    new_value: int
    similarity: float = 0.0  # 0..1, how alike the two leaf names are
    reason: str = "value"  # "value": same unique value; "position": same slot between stable neighbours

    @property
    def value(self) -> int:
        return self.new_value


@dataclass(frozen=True)
class Shift:
    """A run of neighbouring fields in one scope that all moved by the same amount."""

    scope: str
    delta: int
    first: str  # leaf names, in old-layout order
    last: str
    count: int
    first_old: int
    first_new: int


@dataclass
class DiffResult:
    added: Offsets = field(default_factory=dict)
    removed: Offsets = field(default_factory=dict)
    changed: list[Change] = field(default_factory=list)
    renamed: list[Rename] = field(default_factory=list)
    shifts: list[Shift] = field(default_factory=list)
    unchanged: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def _pattern(p: str) -> str:
    return p if any(c in p for c in "*?[") else f"*{p}*"


def select(
    offsets: Offsets, include: list[str], exclude: list[str], only: str | None = None
) -> Offsets:
    """Keep keys matching any ``include`` glob and no ``exclude`` glob (case-insensitive).

    A pattern without glob characters is treated as a substring match. ``only`` narrows to
    ``"methods"`` (keys with a parameter list, like ``A.B.M(int)``) or ``"fields"`` (the rest).
    """
    if only not in (None, "fields", "methods"):
        raise ValueError(f"only must be 'fields' or 'methods', not {only!r}")
    inc = [_pattern(p).lower() for p in include]
    exc = [_pattern(p).lower() for p in exclude]
    out: Offsets = {}
    for key, value in offsets.items():
        if only and ("(" in key) != (only == "methods"):
            continue
        # match the full key or any dotted tail of it, so "m_i*" hits "client.dll.m_iHealth"
        low = key.lower()
        tails = [low] + [low[i + 1:] for i, ch in enumerate(low) if ch == "."]
        if inc and not any(fnmatchcase(t, p) for t in tails for p in inc):
            continue
        if any(fnmatchcase(t, p) for t in tails for p in exc):
            continue
        out[key] = value
    return out


EDGE_MIN_SIMILARITY = 0.6  # a slot at the edge of a scope has one stable neighbour, so names must help


def _scope(key: str) -> str:
    """Parent scope of a key. Method keys (``A.B.M(System.String)``) keep their parameter
    list out of the split and get a scope of their own, apart from the class's fields."""
    head, paren, _ = key.partition("(")
    return head.rpartition(".")[0] + ("()" if paren else "")


def _renames_by_value(added: Offsets, removed: Offsets) -> list[Rename]:
    """Pair a removed key with an added key when they share a unique non-zero value."""
    added_counts = Counter(added.values())
    removed_counts = Counter(removed.values())
    by_value = {v: k for k, v in added.items()}
    renames = []
    for old_key, value in removed.items():
        if value and added_counts[value] == 1 and removed_counts[value] == 1:
            new_key = by_value[value]
            renames.append(
                Rename(old_key, new_key, value, value, _name_similarity(old_key, new_key), "value")
            )
    return renames


def _gaps(items: list[tuple[int, str]], common: set[str], pool: Offsets, anchors: int) -> list[list[str]]:
    """Split a scope's value-ordered keys into gaps separated by keys present in both dumps."""
    gaps: list[list[str]] = [[] for _ in range(anchors + 1)]
    index = 0
    for _, key in items:
        if key in common:
            index += 1
        elif key in pool:
            gaps[index].append(key)
    return gaps


def _renames_by_position(
    old: Offsets, new: Offsets, removed: Offsets, added: Offsets
) -> list[Rename]:
    """Catch renames whose value moved, e.g. a field renamed in a struct that also shifted.

    Within one scope, keys present in both dumps are anchors. If the anchors keep the same
    relative order and exactly one key vanished from a slot where exactly one appeared, it
    is the same slot under a new name. A scope whose anchors were reordered is skipped.
    """
    common = old.keys() & new.keys()
    scopes = {_scope(k) for k in removed} & {_scope(k) for k in added}
    renames = []
    for scope in sorted(scopes):
        old_items = sorted((v, k) for k, v in old.items() if _scope(k) == scope)
        new_items = sorted((v, k) for k, v in new.items() if _scope(k) == scope)
        old_anchors = [k for _, k in old_items if k in common]
        if old_anchors != [k for _, k in new_items if k in common]:
            continue
        gaps_old = _gaps(old_items, common, removed, len(old_anchors))
        gaps_new = _gaps(new_items, common, added, len(old_anchors))
        for i, (gone, came) in enumerate(zip(gaps_old, gaps_new)):
            if len(gone) != 1 or len(came) != 1:
                continue
            similarity = _name_similarity(gone[0], came[0])
            interior = 0 < i < len(old_anchors)
            if interior or similarity >= EDGE_MIN_SIMILARITY:
                renames.append(
                    Rename(gone[0], came[0], old[gone[0]], new[came[0]], similarity, "position")
                )
    return renames


def _detect_renames(
    old: Offsets, new: Offsets, added: Offsets, removed: Offsets
) -> list[Rename]:
    renames = _renames_by_value(added, removed)
    taken_old = {r.old_key for r in renames}
    taken_new = {r.new_key for r in renames}
    renames += _renames_by_position(
        old,
        new,
        {k: v for k, v in removed.items() if k not in taken_old},
        {k: v for k, v in added.items() if k not in taken_new},
    )
    # most convincing first: similar names on top, the rest alphabetical
    return sorted(renames, key=lambda r: (-r.similarity, r.old_key))


def _layout_shifts(old: Offsets, new: Offsets, changed: list[Change]) -> list[Shift]:
    """Find runs of two or more neighbouring fields that moved by the same non-zero amount.

    That is what a struct growing (or shrinking) looks like: everything after the insertion
    point shifts together. Fields are walked in old-layout order per scope, and an unchanged
    field breaks a run, so two separate shifts are never merged across it. Methods are left
    out: RVAs move independently, so a shared delta there would be a coincidence.
    """
    delta_of = {c.key: c.delta for c in changed}
    by_scope: dict[str, list[tuple[int, str]]] = {}
    for key, value in old.items():
        if "(" not in key and key in new:
            by_scope.setdefault(_scope(key), []).append((value, key))

    shifts = []
    for scope, items in by_scope.items():
        items.sort()
        cut = len(scope) + 1 if scope else 0
        for delta, run in groupby(items, key=lambda item: delta_of.get(item[1], 0)):
            run = list(run)
            if delta and len(run) >= 2:
                first, last = run[0], run[-1]
                shifts.append(
                    Shift(scope, delta, first[1][cut:], last[1][cut:], len(run), first[0], new[first[1]])
                )
    return sorted(shifts, key=lambda s: (-s.count, s.scope, s.first_old))


def _name_similarity(old_key: str, new_key: str) -> float:
    leaf = lambda key: key.partition("(")[0].rsplit(".", 1)[-1].lower()
    return SequenceMatcher(None, leaf(old_key), leaf(new_key)).ratio()


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

    result.renamed = _detect_renames(old, new, result.added, result.removed)
    result.shifts = _layout_shifts(old, new, result.changed)
    return result
