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

    def test_position_rename_when_layout_shifted(self):
        old = {"c.m_a": 0x10, "c.m_old": 0x18, "c.m_z": 0x20}
        new = {"c.m_a": 0x14, "c.m_new": 0x1C, "c.m_z": 0x24}
        r = diff(old, new)
        self.assertEqual(len(r.renamed), 1)
        rn = r.renamed[0]
        self.assertEqual((rn.old_key, rn.new_key, rn.reason), ("c.m_old", "c.m_new", "position"))
        self.assertEqual((rn.old_value, rn.new_value), (0x18, 0x1C))
        self.assertEqual({c.key for c in r.changed}, {"c.m_a", "c.m_z"})

    def test_value_match_wins_over_position(self):
        r = diff({"c.a": 1, "c.old": 5, "c.z": 9}, {"c.a": 1, "c.new": 5, "c.z": 9})
        self.assertEqual([x.reason for x in r.renamed], ["value"])

    def test_position_skipped_when_anchors_reordered(self):
        r = diff({"c.a": 1, "c.b": 2, "c.x": 3}, {"c.b": 1, "c.a": 2, "c.y": 4})
        self.assertEqual(r.renamed, [])

    def test_position_needs_one_to_one_slot(self):
        old = {"c.a": 1, "c.x1": 2, "c.x2": 3, "c.z": 9}
        new = {"c.a": 1, "c.y": 7, "c.z": 9}
        self.assertEqual(diff(old, new).renamed, [])

    def test_edge_slot_needs_similar_names(self):
        self.assertEqual(diff({"c.a": 1, "c.x": 5}, {"c.a": 1, "c.zzz": 9}).renamed, [])
        r = diff({"c.a": 1, "c.m_health": 5}, {"c.a": 1, "c.m_healthx": 9})
        self.assertEqual([x.reason for x in r.renamed], ["position"])

    def test_scopes_are_independent(self):
        old = {"p.a": 1, "p.old": 2, "p.z": 3, "q.a": 1, "q.z": 3}
        new = {"p.a": 1, "p.new": 2, "p.z": 3, "q.a": 1, "q.z": 3}
        r = diff(old, new)
        self.assertEqual([(x.old_key, x.new_key) for x in r.renamed], [("p.old", "p.new")])

    def test_zero_is_not_a_rename(self):
        self.assertEqual(diff({"a": 0}, {"b": 0}).renamed, [])

    def test_select(self):
        data = {"client.dll.dwViewMatrix": 1, "client.dll.m_iHealth": 2, "engine.dll.x": 3}
        self.assertEqual(set(select(data, ["client"], [])), {"client.dll.dwViewMatrix", "client.dll.m_iHealth"})
        self.assertEqual(set(select(data, [], ["m_i*", "engine"])), {"client.dll.dwViewMatrix"})


class HtmlTests(unittest.TestCase):
    def render(self, old, new):
        from offset_diff.report import render_html

        return render_html(diff(old, new), "old<1>.json", "new&2.json")

    def test_sections_and_values(self):
        page = self.render(
            {"c.m_a": 0x10, "c.m_old": 0x18, "c.m_z": 0x20, "c.gone": 0x99, "x.same": 5},
            {"c.m_a": 0x14, "c.m_new": 0x1C, "c.m_z": 0x24, "c.fresh": 0x77, "x.same": 5},
        )
        for needle in ("Changed (2)", "Possible renames (1)", "Added (1)", "Removed (1)"):
            self.assertIn(needle, page)
        self.assertIn("+0x4", page)
        self.assertIn("c.m_old", page)
        self.assertIn("same position", page)
        # the renamed pair is not repeated under Added / Removed
        self.assertEqual(page.count("c.m_new"), 1)
        self.assertEqual(page.count("c.m_old"), 1)
        self.assertIn("c.fresh", page)
        self.assertIn("c.gone", page)

    def test_everything_user_supplied_is_escaped(self):
        page = self.render({"<script>a": 1}, {"<img src=x onerror=1>": 2})
        self.assertNotIn("<script>a", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;a", page)
        self.assertIn("old&lt;1&gt;.json", page)
        self.assertIn("new&amp;2.json", page)

    def test_standalone_and_empty(self):
        page = self.render({"a": 1}, {"a": 1})
        self.assertIn("No differences.", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("<table>", page)
        self.assertTrue(page.startswith("<!doctype html>"))

    def test_cli_html_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, out = (Path(tmp) / n for n in ("a.json", "b.json", "r.html"))
            a.write_text('{"x": 1}')
            b.write_text('{"x": 2}')
            self.assertEqual(main([str(a), str(b), "-f", "html", "-o", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("<td", text)
            self.assertIn("0x2", text)


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
