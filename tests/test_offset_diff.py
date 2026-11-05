import json
import tempfile
import unittest
from pathlib import Path

from offset_diff.cli import main
from offset_diff.diff import diff, select
from offset_diff.parse import load, parse_json, parse_source, parse_text

HPP = """
// generated
#pragma once
#include <cstddef>
namespace cs2_dumper {
    namespace offsets {
        namespace client_dll {
            constexpr std::ptrdiff_t dwLocalPlayerPawn = 0x1BE7DA0; // comment
            constexpr std::ptrdiff_t dwViewMatrix = 0x1E2C650;
        }
        namespace engine2_dll {
            constexpr std::ptrdiff_t dwBuildNumber = 0x540BE4;
        }
    }
}
"""

RUST = """
pub mod cs2_dumper {
    pub mod offsets {
        pub mod client_dll {
            pub const dwViewMatrix: usize = 0x1E2C650;
        }
    }
}
"""

CSHARP = """
namespace CS2Dumper.Offsets {
    public static class ClientDll {
        public const nint dwViewMatrix = 0x1E2C650;
    }
}
"""


class ParseTests(unittest.TestCase):
    def test_json_flatten(self):
        data = {"client.dll": {"dwViewMatrix": 100, "x": {"y": "0x10"}}, "skip": "abc", "b": True}
        self.assertEqual(
            parse_json(json.dumps(data)),
            {"client.dll.dwViewMatrix": 100, "client.dll.x.y": 16},
        )

    def test_cpp_namespaces(self):
        got = parse_source(HPP)
        self.assertEqual(got["cs2_dumper.offsets.client_dll.dwLocalPlayerPawn"], 0x1BE7DA0)
        self.assertEqual(got["cs2_dumper.offsets.engine2_dll.dwBuildNumber"], 0x540BE4)
        self.assertEqual(len(got), 3)

    def test_rust(self):
        self.assertEqual(
            parse_source(RUST), {"cs2_dumper.offsets.client_dll.dwViewMatrix": 0x1E2C650}
        )

    def test_csharp(self):
        self.assertEqual(
            parse_source(CSHARP), {"CS2Dumper.Offsets.ClientDll.dwViewMatrix": 0x1E2C650}
        )

    def test_sniff_without_hint(self):
        self.assertEqual(parse_text('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_text("constexpr int a = 2;"), {"a": 2})

    def test_load_dir_prefers_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "offsets.json").write_text('{"client.dll": {"dwX": 1}}')
            (d / "offsets.hpp").write_text("constexpr int dwX = 999;")
            self.assertEqual(load(d), {"offsets.client.dll.dwX": 1})


class DiffTests(unittest.TestCase):
    def test_classification(self):
        old = {"a": 1, "b": 2, "c": 3, "same": 9}
        new = {"a": 1, "b": 20, "d": 4, "same": 9}
        r = diff(old, new)
        self.assertEqual([(c.key, c.delta) for c in r.changed], [("b", 18)])
        self.assertEqual(r.added, {"d": 4})
        self.assertEqual(r.removed, {"c": 3})
        self.assertEqual(r.unchanged, 2)
        self.assertTrue(r.has_changes)

    def test_min_delta(self):
        r = diff({"a": 0x100}, {"a": 0x108}, min_delta=0x10)
        self.assertFalse(r.has_changes)
        self.assertEqual(r.unchanged, 1)

    def test_rename_needs_unique_value(self):
        r = diff({"old_name": 0x50}, {"new_name": 0x50})
        self.assertEqual([(x.old_key, x.new_key) for x in r.renamed], [("old_name", "new_name")])
        r = diff({"p": 0x50, "q": 0x50}, {"n": 0x50})
        self.assertEqual(r.renamed, [])

    def test_rename_similarity_ranks_results(self):
        r = diff(
            {"m.dwEntityList": 0x10, "m.dwFoo": 0x20},
            {"m.dwEntityListEx": 0x10, "m.zzzzzz": 0x20},
        )
        self.assertEqual([x.old_key for x in r.renamed], ["m.dwEntityList", "m.dwFoo"])
        self.assertGreater(r.renamed[0].similarity, 0.8)
        self.assertLess(r.renamed[1].similarity, 0.3)

    def test_rename_similarity_in_reports(self):
        from offset_diff.report import render_json, render_text

        r = diff({"a.dwOld": 0x50}, {"a.dwOldX": 0x50})
        self.assertIn("name 91%", render_text(r, "a", "b"))
        self.assertEqual(json.loads(render_json(r, "a", "b"))["renamed"][0]["similarity"], 0.909)

    def test_zero_is_not_a_rename(self):
        self.assertEqual(diff({"a": 0}, {"b": 0}).renamed, [])

    def test_select(self):
        data = {"client.dll.dwViewMatrix": 1, "client.dll.m_iHealth": 2, "engine.dll.x": 3}
        self.assertEqual(set(select(data, ["client"], [])), {"client.dll.dwViewMatrix", "client.dll.m_iHealth"})
        self.assertEqual(set(select(data, [], ["m_i*", "engine"])), {"client.dll.dwViewMatrix"})


class CliTests(unittest.TestCase):
    def test_fail_on_change_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, out = (Path(tmp) / n for n in ("a.json", "b.json", "r.txt"))
            a.write_text('{"x": 1}')
            b.write_text('{"x": 2}')
            self.assertEqual(main([str(a), str(b), "-o", str(out)]), 0)
            self.assertIn("CHANGED", out.read_text(encoding="utf-8"))
            self.assertEqual(main([str(a), str(b), "-o", str(out), "--fail-on-change"]), 1)
            self.assertEqual(main([str(a), str(a), "-o", str(out), "--fail-on-change"]), 0)
            self.assertEqual(main([str(a), str(Path(tmp) / "nope.json")]), 2)


if __name__ == "__main__":
    unittest.main()
