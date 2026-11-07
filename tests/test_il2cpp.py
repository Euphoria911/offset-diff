import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from offset_diff.cli import main
from offset_diff.diff import diff, select
from offset_diff.il2cpp import parse_il2cpp
from offset_diff.parse import load, parse_text

DUMP_A = """\
// Namespace: Game
public class Player : MonoBehaviour // TypeDefIndex: 100
{
	// Fields
	public int health; // 0x18
	private int oldArmor; // 0x1C
	private float speed; // 0x20
	public const int Max = 100;
	public static Dictionary<int, string> Names; // 0x0

	// Methods

	// RVA: 0x1A2B3C Offset: 0x1A1B3C VA: 0x1A2B3C
	public void TakeDamage(int amount, ref Vector3 dir) { }

	[CompilerGeneratedAttribute] // RVA: 0x2000 Offset: 0x1F00 VA: 0x2000
	private void <Update>b__0(string s = "x") { }

	// RVA: 0x3000 Offset: 0x2F00 VA: 0x3000
	[DebuggerStepThrough]
	public Dictionary<int, string> Lookup(List<System.Int32> ids) { }

	// RVA: -1 Offset: -1
	public abstract void Abstract();
}

// Namespace:
public struct Vec // TypeDefIndex: 101
{
	public float x; // 0x0
	// RVA: 0x4000 Offset: 0x3F00 VA: 0x4000
	public void .ctor() { }
}
"""

# next build: struct grew by 8 bytes, oldArmor renamed, TakeDamage moved
DUMP_B = DUMP_A.replace("0x18", "0x20").replace("0x1C", "0x24").replace(
    "private float speed; // 0x20", "private float speed; // 0x28"
).replace("oldArmor", "armorValue").replace("0x1A2B3C Offset", "0x1B0000 Offset")


class ParseTests(unittest.TestCase):
    def test_fields_and_methods(self):
        got = parse_il2cpp(DUMP_A)
        self.assertEqual(got["Game.Player.health"], 0x18)
        self.assertEqual(got["Game.Player.speed"], 0x20)
        self.assertEqual(got["Game.Player.Names"], 0)
        self.assertEqual(got["Game.Player.TakeDamage(int, ref Vector3)"], 0x1A2B3C)
        self.assertEqual(got["Vec.x"], 0)
        self.assertEqual(got["Vec..ctor()"], 0x4000)

    def test_attribute_on_rva_line_and_between(self):
        got = parse_il2cpp(DUMP_A)
        self.assertEqual(got["Game.Player.<Update>b__0(string)"], 0x2000)  # default value dropped
        self.assertEqual(got["Game.Player.Lookup(List<System.Int32>)"], 0x3000)

    def test_constants_and_rva_minus_one_skipped(self):
        got = parse_il2cpp(DUMP_A)
        self.assertNotIn("Game.Player.Max", got)
        self.assertFalse(any("Abstract" in k for k in got))
        self.assertEqual(len(got), 9)

    def test_sniffed_without_hint(self):
        self.assertEqual(parse_text(DUMP_A, ".cs"), parse_il2cpp(DUMP_A))
        self.assertEqual(parse_text(DUMP_A), parse_il2cpp(DUMP_A))

    def test_load_file_and_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dump.cs"
            path.write_text(DUMP_A, encoding="utf-8")
            self.assertEqual(load(path), parse_il2cpp(DUMP_A))
            self.assertEqual(load(tmp)["dump.Vec.x"], 0)


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.result = diff(parse_il2cpp(DUMP_A), parse_il2cpp(DUMP_B))

    def test_field_shift_and_method_move(self):
        changed = {c.key: c.delta for c in self.result.changed}
        self.assertEqual(changed["Game.Player.health"], 8)
        self.assertEqual(changed["Game.Player.speed"], 8)
        self.assertEqual(changed["Game.Player.TakeDamage(int, ref Vector3)"], 0x1B0000 - 0x1A2B3C)

    def test_renamed_field_found_by_position(self):
        pairs = [(r.old_key, r.new_key, r.reason) for r in self.result.renamed]
        self.assertEqual(
            pairs, [("Game.Player.oldArmor", "Game.Player.armorValue", "position")]
        )

    def test_dotted_parameter_types_do_not_break_scopes(self):
        old = {"A.B.M(System.String)": 0x100, "A.B.N(System.Int32)": 0x200, "A.B.f": 4}
        new = {"A.B.M(System.String)": 0x100, "A.B.N(System.Int32)": 0x208, "A.B.f": 4}
        r = diff(old, new)
        self.assertEqual([c.key for c in r.changed], ["A.B.N(System.Int32)"])
        self.assertEqual(r.renamed, [])

    def test_fields_and_methods_are_separate_scopes(self):
        # method RVAs reorder between builds; that must not disable field rename detection
        old = {"A.B.f1": 8, "A.B.old": 12, "A.B.f2": 16, "A.B.M1()": 0x500, "A.B.M2()": 0x600}
        new = {"A.B.f1": 8, "A.B.new": 14, "A.B.f2": 16, "A.B.M1()": 0x700, "A.B.M2()": 0x650}
        r = diff(old, new)
        self.assertEqual([(x.old_key, x.new_key) for x in r.renamed], [("A.B.old", "A.B.new")])

    def test_exclude_methods_with_glob(self):
        kept = select(parse_il2cpp(DUMP_A), [], ["*(*"])
        self.assertTrue(kept)
        self.assertFalse(any("(" in k for k in kept))


class OnlyTests(unittest.TestCase):
    def test_select_only_fields_or_methods(self):
        data = parse_il2cpp(DUMP_A)
        fields = select(data, [], [], only="fields")
        methods = select(data, [], [], only="methods")
        self.assertEqual(len(fields), 5)
        self.assertEqual(len(methods), 4)
        self.assertEqual(fields.keys() | methods.keys(), data.keys())
        self.assertTrue(all("(" in k for k in methods))
        self.assertFalse(any("(" in k for k in fields))

    def test_only_combines_with_include(self):
        got = select(parse_il2cpp(DUMP_A), ["Player"], [], only="methods")
        self.assertEqual(len(got), 3)

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            select({}, [], [], only="vtables")

    def test_cli_only_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.cs", Path(tmp) / "b.cs"
            a.write_text(DUMP_A, encoding="utf-8")
            b.write_text(DUMP_B, encoding="utf-8")
            for kind, wanted, unwanted in (
                ("methods", "TakeDamage", "Game.Player.health"),
                ("fields", "Game.Player.health", "TakeDamage"),
            ):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main([str(a), str(b), "--only", kind]), 0)
                self.assertIn(wanted, out.getvalue())
                self.assertNotIn(unwanted, out.getvalue())


class CliTests(unittest.TestCase):
    def test_cli_diff_of_two_dumps(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.cs", Path(tmp) / "b.cs"
            a.write_text(DUMP_A, encoding="utf-8")
            b.write_text(DUMP_B, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main([str(a), str(b), "-x", "*(*", "--fail-on-change"])
            self.assertEqual(code, 1)
            text = out.getvalue()
            self.assertIn("Game.Player.health", text)
            self.assertIn("oldArmor -> Game.Player.armorValue", text)
            self.assertNotIn("TakeDamage", text)


if __name__ == "__main__":
    unittest.main()
