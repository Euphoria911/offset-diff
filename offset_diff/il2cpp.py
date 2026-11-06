"""Parser for Il2CppDumper ``dump.cs``.

Keys produced:
  * instance/static fields: ``Namespace.Class.field``              -> field offset
  * methods with an RVA:    ``Namespace.Class.Method(type, type)`` -> RVA

Parameter names are dropped from method keys so a renamed argument is not a new method.
Constants have no offset in the dump and are skipped.
"""

from __future__ import annotations

import re

Offsets = dict[str, int]

MARKER = "// TypeDefIndex:"

_NS_RE = re.compile(r"^//\s*Namespace:\s*(.*?)\s*$")
_TYPE_RE = re.compile(r"\b(?:class|struct|enum|interface)\s+(?P<name>[^\s:{/]+).*//\s*TypeDefIndex:")
_FIELD_RE = re.compile(
    r"^\s+[^/;=()]+?\s+(?P<name>[A-Za-z_][\w<>]*)\s*;\s*//\s*0x(?P<off>[0-9A-Fa-f]+)\s*$"
)
_RVA_RE = re.compile(r"//\s*RVA:\s*0x(?P<rva>[0-9A-Fa-f]+)")


def _split_params(text: str) -> list[str]:
    """Split on top-level commas only, so ``Dictionary<int, string> d`` stays one parameter."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "<[":
            depth += 1
        elif ch in ">]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _param_type(param: str) -> str:
    tokens = param.split("=")[0].split()  # drop any default value, then the trailing name
    return " ".join(tokens[:-1]) if len(tokens) > 1 else " ".join(tokens)


def _method_key(signature: str) -> str | None:
    start, end = signature.find("("), signature.rfind(")")
    if start < 0 or end < start:
        return None
    head = signature[:start].split()
    if not head:
        return None
    types = ", ".join(_param_type(p) for p in _split_params(signature[start + 1 : end]))
    return f"{head[-1]}({types})"


def parse_il2cpp(text: str) -> Offsets:
    out: Offsets = {}
    namespace = ""
    cls: str | None = None
    rva: int | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if (m := _NS_RE.match(stripped)) is not None:
            namespace = m.group(1)
            continue
        if not stripped.startswith("//") and (m := _TYPE_RE.search(line)) is not None:
            cls, rva = m.group("name"), None
            continue
        if cls is None:
            continue
        scope = f"{namespace}.{cls}" if namespace else cls

        if (m := _RVA_RE.search(line)) is not None:
            rva = int(m.group("rva"), 16)
            continue
        if rva is not None:
            # the signature follows the RVA comment, possibly after attribute lines
            if not stripped or stripped.startswith(("//", "[")):
                continue
            if (key := _method_key(stripped)) is not None:
                out[f"{scope}.{key}"] = rva
            rva = None
            continue
        if (m := _FIELD_RE.match(line)) is not None:
            out[f"{scope}.{m.group('name')}"] = int(m.group("off"), 16)
    return out
