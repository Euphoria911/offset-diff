import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from offset_diff.cli import main
from offset_diff.gitsource import load_rev, split_spec


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        check=True, capture_output=True,
    )


def put(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls._tmp.name)
        git(cls.repo, "init", "-q")
        put(cls.repo, "offsets.json", '{"c": {"a": 1, "b": 2}}')
        put(cls.repo, "output/client.json", '{"m_a": 16}')
        put(cls.repo, "output/client.hpp", "namespace x { constexpr int m_a = 99; }")  # JSON wins
        put(cls.repo, "output/notes.md", "ignored")
        put(cls.repo, "dump.cs", "// Namespace: G\npublic class P // TypeDefIndex: 1\n{\n\tpublic int hp; // 0x10\n}\n")
        git(cls.repo, "add", "-A")
        git(cls.repo, "commit", "-q", "-m", "one")
        git(cls.repo, "tag", "v1")
        put(cls.repo, "offsets.json", '{"c": {"a": 1, "b": 5, "d": 9}}')
        put(cls.repo, "output/client.json", '{"m_a": 24}')
        git(cls.repo, "commit", "-q", "-am", "two")

    @classmethod
    def tearDownClass(cls):
        # git marks object files read-only on Windows
        shutil.rmtree(cls._tmp.name, onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))

    def load(self, spec, default_path=None):
        return load_rev(str(self.repo), spec, default_path)

    def test_file_at_two_revisions(self):
        self.assertEqual(self.load("v1:offsets.json"), {"c.a": 1, "c.b": 2})
        self.assertEqual(self.load("HEAD:offsets.json"), {"c.a": 1, "c.b": 5, "c.d": 9})
        self.assertEqual(self.load("HEAD~1:offsets.json"), self.load("v1:offsets.json"))

    def test_bare_revision_with_default_path(self):
        self.assertEqual(self.load("v1", "offsets.json"), {"c.a": 1, "c.b": 2})
        self.assertEqual(self.load("v1:offsets.json", "ignored.json"), {"c.a": 1, "c.b": 2})

    def test_folder_prefers_json_and_skips_other_files(self):
        self.assertEqual(self.load("v1:output"), {"client.m_a": 16})
        self.assertEqual(self.load("HEAD:output/"), {"client.m_a": 24})

    def test_repo_root_folder(self):
        got = self.load("v1:")
        self.assertEqual(got["offsets.c.b"], 2)
        self.assertEqual(got["dump.G.P.hp"], 0x10)  # dump.cs is picked up by stem
        self.assertNotIn("notes", " ".join(got))

    def test_source_blob_is_parsed_by_suffix(self):
        self.assertEqual(self.load("v1:dump.cs"), {"G.P.hp": 0x10})

    def test_errors_are_value_errors(self):
        for spec in ("nope:offsets.json", "v1:missing.json", "-bad:offsets.json", ":offsets.json"):
            with self.assertRaises(ValueError, msg=spec):
                self.load(spec)
        with self.assertRaises(ValueError):
            self.load("v1")  # no path anywhere
        with self.assertRaises(ValueError):
            load_rev(str(self.repo / "not-a-repo"), "v1:offsets.json")

    def test_split_spec(self):
        self.assertEqual(split_spec("v1:a/b.json"), ("v1", "a/b.json"))
        self.assertEqual(split_spec("v1:/a/"), ("v1", "a"))
        self.assertEqual(split_spec("HEAD~2", "x/y.json"), ("HEAD~2", "x/y.json"))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitCliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.tmp.name)
        git(cls.repo, "init", "-q")
        put(cls.repo, "o.json", '{"x": 1, "y": 2}')
        git(cls.repo, "add", "-A")
        git(cls.repo, "commit", "-q", "-m", "one")
        git(cls.repo, "tag", "old")
        put(cls.repo, "o.json", '{"x": 1, "y": 7, "z": 3}')
        git(cls.repo, "commit", "-q", "-am", "two")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp.name, onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))

    def test_diff_between_revisions(self):
        code, out, _ = self.run_cli("old:o.json", "HEAD:o.json", "--repo", str(self.repo), "-f", "json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([(c["key"], c["delta"]) for c in data["changed"]], [("y", 5)])
        self.assertEqual(data["added"], {"z": 3})
        self.assertEqual(data["old"], "old:o.json")

    def test_bare_revisions_with_path(self):
        code, out, _ = self.run_cli("old", "HEAD", "--repo", str(self.repo), "-p", "o.json", "--fail-on-change")
        self.assertEqual(code, 1)
        self.assertIn("y", out)
        self.assertEqual(
            self.run_cli("old", "old", "--repo", str(self.repo), "-p", "o.json", "--fail-on-change")[0], 0
        )

    def test_bad_input_exits_2_or_errors(self):
        code, _, err = self.run_cli("nope:o.json", "HEAD:o.json", "--repo", str(self.repo))
        self.assertEqual(code, 2)
        self.assertIn("offset-diff:", err)
        self.assertEqual(self.run_cli("old", "HEAD", "--repo", str(self.repo))[0], 2)  # no --path
        self.assertEqual(self.run_cli("old", "HEAD", "-p", "o.json")[0], 2)  # --path without --repo


if __name__ == "__main__":
    unittest.main()
