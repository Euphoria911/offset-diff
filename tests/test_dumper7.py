import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from offset_diff.cli import main
from offset_diff.diff import diff
from offset_diff.dumper7 import is_dumper7, parse_dumper7
from offset_diff.parse import load, parse_text

# struct name -> (sizeof, alignof, {member: offset}); mirrors what Dumper-7 measured
Structs = dict[str, tuple[int, int, dict[str, int]]]

BUILD_A: Structs = {
    "UObject": (0x28, 8, {"Flags": 0x08, "Class": 0x10, "Name": 0x18, "Outer": 0x20}),
    "AActor": (0x330, 8, {"Owner": 0x120, "Instigator": 0x128, "RootComponent": 0x168,
                          "Tags": 0x1A0, "Extra": 0x1B0}),
    "UWidget": (0x60, 8, {"Slot": 0x28, "OldName": 0x30, "Visible": 0x38}),
    "Params::Actor_ReceiveTick": (0x4, 4, {"DeltaSeconds": 0x0}),
    "FVector": (0x18, 8, {"X": 0x0, "Y": 0x8, "Z": 0x10}),
}

# next build: AActor grew by 8 bytes after Instigator, UWidget::OldName renamed (and moved),
# the Params struct is gone, and there is a new class
BUILD_B: Structs = {
    "UObject": (0x28, 8, {"Flags": 0x08, "Class": 0x10, "Name": 0x18, "Outer": 0x20}),
    "AActor": (0x338, 8, {"Owner": 0x120, "Instigator": 0x128, "RootComponent": 0x170,
                          "Tags": 0x1A8, "Extra": 0x1B8}),
    "UWidget": (0x60, 8, {"Slot": 0x28, "NewName": 0x34, "Visible": 0x38}),
    "FVector": (0x18, 8, {"X": 0x0, "Y": 0x8, "Z": 0x10}),
    "UNewThing": (0x40, 8, {"Value": 0x30}),
}


def assertions_inl(structs: Structs, macro: bool = True) -> str:
    """Text laid out like the generator's `Assertions.inl` (macro) or its inline form."""
    nl = " \\\n" if macro else "\n"
    out = ["//\n// Debug assertions to verify member-offsets and struct-sizes\n//\n\n"]
    for name, (size, align, members) in structs.items():
        if macro:
            out.append(f"#define DUMPER7_ASSERTS_{name.replace('::', '_')} \\\n")
        out.append(f'static_assert(alignof({name}) == 0x{align:06X}, "Wrong alignment on {name}");{nl}')
        out.append(f'static_assert(sizeof({name}) == 0x{size:06X}, "Wrong size on {name}");{nl}')
        for member, offset in members.items():
            out.append(
                f"static_assert(offsetof({name}, {member}) == 0x{offset:06X}, "
                f"\"Member '{name}::{member}' has a wrong offset!\");{nl}"
            )
        out.append("\n")
    return "".join(out)


BASIC_HPP = """\
#pragma once
/*
* Disclaimer:
*	- The 'GNames' is only a fallback and null by default, FName::AppendString is used
*/
namespace Offsets
{
	constexpr int32 GObjects             = 0x%08X;
	constexpr int32 AppendString         = 0x%08X;
	constexpr int32 GNames               = 0x00000000;
	constexpr int32 GWorld               = 0x%08X;
	constexpr int32 ProcessEvent         = 0x%08X;
	constexpr int32 ProcessEventIdx      = 0x00000044;
}
"""


class ParseTests(unittest.TestCase):
    def test_members_and_sizes(self):
        got = parse_dumper7(assertions_inl(BUILD_A))
        self.assertEqual(got["UObject.Class"], 0x10)
        self.assertEqual(got["AActor.RootComponent"], 0x168)
        self.assertEqual(got["UObject.sizeof"], 0x28)
        self.assertEqual(got["AActor.sizeof"], 0x330)
        self.assertEqual(got["Params::Actor_ReceiveTick.DeltaSeconds"], 0)
        self.assertEqual(got["FVector.Z"], 0x10)
        self.assertEqual(len(got), sum(len(m) + 1 for _, _, m in BUILD_A.values()))  # members + sizeof each

    def test_alignment_and_macro_lines_are_not_keys(self):
        got = parse_dumper7(assertions_inl(BUILD_A))
        self.assertFalse(any("align" in k.lower() or "DUMPER7" in k for k in got))

    def test_macro_and_inline_forms_agree(self):
        self.assertEqual(
            parse_dumper7(assertions_inl(BUILD_A, macro=True)),
            parse_dumper7(assertions_inl(BUILD_A, macro=False)),
        )

    def test_odd_but_possible_names(self):
        text = (
            'static_assert(sizeof(TWeakObjectPtr<UObject>) == 0x000008, "Wrong size");\n'
            'static_assert(offsetof(  FFoo  ,  Bar_0  ) ==   0x0000A0 ,"x");\n'
            'static_assert(offsetof(FFoo, Baz) == 16, "decimal");\n'
        )
        self.assertEqual(
            parse_dumper7(text),
            {"TWeakObjectPtr<UObject>.sizeof": 8, "FFoo.Bar_0": 0xA0, "FFoo.Baz": 16},
        )

    def test_unrelated_static_asserts_are_ignored(self):
        text = (
            'static_assert(std::is_integral_v<T>, "must be integral");\n'
            'static_assert(alignof(X) == 0x000008, "Wrong alignment on X");\n'
            'static_assert(GetInterfaceOffset<A, B>() == 0x0010, "Wrong interface VFT offset");\n'
        )
        self.assertEqual(parse_dumper7(text), {})
        self.assertFalse(is_dumper7(text))

    def test_detection(self):
        self.assertTrue(is_dumper7(assertions_inl(BUILD_A)))
        self.assertFalse(is_dumper7(BASIC_HPP))
        self.assertFalse(is_dumper7('{"a": 1}'))


class RoutingTests(unittest.TestCase):
    def test_parse_text_routes_by_content_with_or_without_a_suffix(self):
        text = assertions_inl(BUILD_A)
        expected = parse_dumper7(text)
        for hint in (".inl", ".hpp", ".txt", ""):
            self.assertEqual(parse_text(text, hint), expected, hint)

    def test_offsets_namespace_is_kept_next_to_the_assertions(self):
        basic = BASIC_HPP % (0x1234560, 0x99AA00, 0x2222000, 0x5555550)
        self.assertEqual(
            parse_text(basic, ".hpp"),
            {
                "Offsets.GObjects": 0x1234560,
                "Offsets.AppendString": 0x99AA00,
                "Offsets.GNames": 0,
                "Offsets.GWorld": 0x2222000,
                "Offsets.ProcessEvent": 0x5555550,
                "Offsets.ProcessEventIdx": 0x44,
            },
        )
        mixed = basic + assertions_inl(BUILD_A, macro=False)  # inline-assertion setting: both in one file
        got = parse_text(mixed, ".hpp")
        self.assertEqual(got["Offsets.GObjects"], 0x1234560)
        self.assertEqual(got["AActor.RootComponent"], 0x168)

    def test_sdk_folder_keys_do_not_depend_on_the_file_a_class_lives_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assertions.inl").write_text(assertions_inl(BUILD_A), encoding="utf-8")
            (root / "NameCollisions.inl").write_text("// nothing measured here\n", encoding="utf-8")
            (root / "Basic.hpp").write_text(BASIC_HPP % (1, 2, 3, 4), encoding="utf-8")
            got = load(root)
            self.assertEqual(got["UObject.Class"], 0x10)  # no "Assertions." prefix
            self.assertEqual(got["Basic.Offsets.GObjects"], 1)
            self.assertNotIn("NameCollisions", " ".join(got))

    def test_single_file_with_a_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Assertions.inl"
            path.write_bytes(b"\xef\xbb\xbf" + assertions_inl(BUILD_A).encode("utf-8"))
            self.assertEqual(load(path)["UObject.Name"], 0x18)


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.old = parse_dumper7(assertions_inl(BUILD_A))
        self.new = parse_dumper7(assertions_inl(BUILD_B))
        self.result = diff(self.old, self.new)

    def test_growth_resize_and_membership(self):
        changed = {c.key: c.delta for c in self.result.changed}
        self.assertEqual(changed["AActor.RootComponent"], 8)
        self.assertEqual(changed["AActor.sizeof"], 8)
        self.assertNotIn("UObject.Class", changed)
        self.assertIn("UNewThing.Value", self.result.added)
        self.assertIn("UNewThing.sizeof", self.result.added)
        self.assertIn("Params::Actor_ReceiveTick.DeltaSeconds", self.result.removed)

    def test_renamed_member_is_found_by_position(self):
        pairs = [(r.old_key, r.new_key, r.reason) for r in self.result.renamed
                 if r.old_key.startswith("UWidget.")]
        self.assertEqual(pairs, [("UWidget.OldName", "UWidget.NewName", "position")])

    def test_layout_shifts_count_members_not_the_struct_size(self):
        (shift,) = [s for s in self.result.shifts if s.scope == "AActor"]
        self.assertEqual((shift.delta, shift.first, shift.last), (8, "RootComponent", "Extra"))
        self.assertEqual(shift.count, 3)  # RootComponent, Tags, Extra; sizeof is not a member
        self.assertEqual((shift.first_old, shift.first_new), (0x168, 0x170))

    def test_cli_on_two_assertion_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a" / "Assertions.inl", Path(tmp) / "b" / "Assertions.inl"
            for path, build in ((a, BUILD_A), (b, BUILD_B)):
                path.parent.mkdir()
                path.write_text(assertions_inl(build), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([str(a), str(b), "-g", "--fail-on-change"])
            text = out.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("LAYOUT SHIFTS", text)
            self.assertRegex(text, r"AActor\s+\+0x8\s+RootComponent \.\. Extra\s+\(3 fields")
            self.assertIn("UWidget.OldName -> UWidget.NewName", text)
            self.assertNotIn("UObject", text)  # identical in both builds, so not mentioned
            with contextlib.redirect_stdout(io.StringIO()):  # --only/-i still narrow it
                self.assertEqual(main([str(a), str(b), "-i", "FVector", "--fail-on-change"]), 0)
            # the same dump against itself is clean
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(a), str(a), "--fail-on-change"]), 0)


if __name__ == "__main__":
    unittest.main()
