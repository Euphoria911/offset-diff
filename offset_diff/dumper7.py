"""Dumper-7 (Unreal Engine SDK generator) layout assertions.

Dumper-7 writes the layout it measured as ``static_assert`` lines, by default into
``Assertions.inl`` as ``DUMPER7_ASSERTS_<Class>`` macros, or inline in the struct headers when
its inline-assertion settings are on::

    static_assert(alignof(UObject) == 0x000008, "Wrong alignment on UObject"); \\
    static_assert(sizeof(UObject) == 0x000028, "Wrong size on UObject"); \\
    static_assert(offsetof(UObject, Class) == 0x000010, "Member 'UObject::Class' has a wrong offset!"); \\

(format taken from the generator's ``CppGenerator.cpp``). Those numbers are exactly what changes
between game builds, so each ``offsetof`` becomes ``Class.Member`` and each ``sizeof`` becomes
``Class.sizeof``. ``alignof`` is not kept: it almost never moves, and a value that is the same
for hundreds of members would only confuse rename detection.
"""

from __future__ import annotations

import re

Offsets = dict[str, int]

SIZEOF_LEAF = "sizeof"  # key suffix for a struct's total size, e.g. "UObject.sizeof"

_MARKER_RE = re.compile(r"static_assert\(\s*(?:offsetof|sizeof)\(")
_ASSERT_RE = re.compile(
    r"static_assert\(\s*(?:"
    r"offsetof\(\s*(?P<cls>[\w:<>]+)\s*,\s*(?P<member>\w+)\s*\)"
    r"|sizeof\(\s*(?P<size_cls>[\w:<>]+)\s*\)"
    r")\s*==\s*(?P<value>0[xX][0-9A-Fa-f]+|\d+)"
)


def is_dumper7(text: str) -> bool:
    return _MARKER_RE.search(text) is not None


def parse_dumper7(text: str) -> Offsets:
    out: Offsets = {}
    for m in _ASSERT_RE.finditer(text):
        if m.group("cls"):
            key = f"{m.group('cls')}.{m.group('member')}"
        else:
            key = f"{m.group('size_cls')}.{SIZEOF_LEAF}"
        out[key] = int(m.group("value"), 0)
    return out
