import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from offset_diff.cli import main
from offset_diff.history import build_parser, collect, format_entry

PATH = "out/offsets.json"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def rmtree_force(path: str) -> None:
    shutil.rmtree(path, onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))


def dump(**client: int) -> str:
    return json.dumps({"client": client})


@unittest.skipUnless(shutil.which("git"), "git not installed")
class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls.repo = Path(cls._tmp)
        git(cls.repo, "init", "-q")
        cls.sha = {}

        def commit(name, files, message):
            for rel, text in files.items():
                target = cls.repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            git(cls.repo, "add", "-A")
            git(cls.repo, "commit", "-q", "-m", message)
            cls.sha[name] = git(cls.repo, "rev-parse", "HEAD")

        commit("c1", {PATH: dump(dwViewMatrix=100, dwEntityList=200, dwLocal=300)}, "build 1")
        commit("c2", {PATH: dump(dwViewMatrix=150, dwEntityList=200, dwLocal=300)}, "build 2")
        commit("docs", {"README.md": "unrelated"}, "docs only")  # never touches PATH
        commit("fmt", {PATH: json.dumps({"client": {"dwViewMatrix": 150, "dwEntityList": 200, "dwLocal": 300}}, indent=4)},
               "reformat")  # touches PATH, moves nothing
        commit("c4", {PATH: dump(dwViewMatrix=150, dwEntityList=250, dwLocal=300, dwNew=400)}, "build 4")
        commit("c5", {PATH: dump(dwViewMatrix=175, dwEntityList=250, dwNew=400)}, "build 5")

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls._tmp)

    def entries(self, **kw):
        entries, warnings = collect(str(self.repo), kw.pop("ref", "HEAD"), PATH, **kw)
        return entries, warnings

    def names(self, entries):
        by_sha = {v: k for k, v in self.sha.items()}
        return [by_sha[e.commit] for e in entries]

    def test_newest_first_and_silent_commits_are_left_out(self):
        entries, warnings = self.entries()
        self.assertEqual(self.names(entries), ["c5", "c4", "c2", "c1"])  # not "fmt", not "docs"
        self.assertEqual(warnings, [])
        self.assertEqual([e.subject for e in entries], ["build 5", "build 4", "build 2", "build 1"])
        self.assertTrue(all(len(e.date) == 10 and e.date[4] == "-" for e in entries))

    def test_each_entry_is_compared_with_the_commit_before_it(self):
        entries, _ = self.entries()
        c5, c4, c2, c1 = entries
        self.assertEqual({c.key: (c.old, c.new) for c in c5.result.changed}, {"client.dwViewMatrix": (150, 175)})
        self.assertEqual(c5.result.removed, {"client.dwLocal": 300})
        self.assertEqual(c4.result.added, {"client.dwNew": 400})
        self.assertEqual({c.key: c.delta for c in c4.result.changed}, {"client.dwEntityList": 50})
        self.assertEqual(c5.base, self.sha["c4"])
        self.assertEqual(c2.base, self.sha["c1"])
        self.assertEqual(c4.base, self.sha["fmt"])  # the commit before it that touched PATH

    def test_first_commit_shows_everything_it_introduced(self):
        c1 = self.entries()[0][-1]
        self.assertIsNone(c1.base)
        self.assertEqual(c1.result.added, {"client.dwViewMatrix": 100, "client.dwEntityList": 200, "client.dwLocal": 300})

    def test_follow_a_single_offset(self):
        entries, _ = self.entries(include=["dwViewMatrix"])
        self.assertEqual(self.names(entries), ["c5", "c2", "c1"])
        self.assertEqual([c.delta for e in entries for c in e.result.changed], [25, 50])
        self.assertEqual(set(entries[0].result.removed), set())  # dwLocal is filtered out

    def test_limit_counts_commits_that_touched_the_path(self):
        # PATH was touched by c1, c2, fmt, c4, c5. The last two: the commit before them is a baseline
        entries, _ = self.entries(limit=2)
        self.assertEqual(self.names(entries), ["c5", "c4"])
        entries, _ = self.entries(limit=3)  # baseline is c2, so fmt (silent), c4 and c5 are compared
        self.assertEqual(self.names(entries), ["c5", "c4"])
        entries, _ = self.entries(limit=4)  # baseline c1: c2 appears, but c1's additions do not
        self.assertEqual(self.names(entries), ["c5", "c4", "c2"])
        for limit in (5, 6, 0):  # the whole history is in range, so the first commit shows too
            self.assertEqual(self.names(self.entries(limit=limit)[0]), ["c5", "c4", "c2", "c1"], limit)

    def test_start_from_an_older_ref(self):
        entries, _ = self.entries(ref=self.sha["c2"])
        self.assertEqual(self.names(entries), ["c2", "c1"])

    def test_exclude_and_min_delta(self):
        entries, _ = self.entries(exclude=["dwEntityList"])
        self.assertEqual(self.names(entries), ["c5", "c4", "c2", "c1"])
        self.assertEqual([c.key for c in entries[1].result.changed], [])  # c4's only change was dwEntityList
        entries, _ = self.entries(min_delta=40)  # keeps +50 and +50, drops +25
        self.assertEqual(self.names(entries)[:2], ["c5", "c4"])
        self.assertEqual([c.key for c in entries[0].result.changed], [])  # only the removal remains
        entries, _ = self.entries(include=["dwViewMatrix"], min_delta=40)
        self.assertEqual(self.names(entries), ["c2", "c1"])

    def test_nothing_in_range_and_a_missing_path(self):
        self.assertEqual(self.entries(include=["no-such-offset"])[0], [])
        entries, _ = collect(str(self.repo), "HEAD", "out/missing.json")
        self.assertEqual(entries, [])

    def test_text_and_json_formats(self):
        c5 = self.entries()[0][0]
        text = format_entry(c5)
        self.assertTrue(text.startswith(f"{c5.commit[:8]}  {c5.date}  build 5\n"))
        self.assertIn("~ client.dwViewMatrix  0x96 -> 0xAF  (+0x19)", text)
        self.assertIn("- client.dwLocal", text)
        data = json.loads(format_entry(c5, "json"))
        self.assertEqual((data["commit"], data["subject"], data["base"]), (c5.commit, "build 5", self.sha["c4"]))
        self.assertEqual(data["changed"], [{"key": "client.dwViewMatrix", "old": 150, "new": 175, "delta": 25}])
        self.assertEqual(data["removed"], {"client.dwLocal": 300})

    def test_bad_ref_is_an_error(self):
        with self.assertRaises(ValueError):
            self.entries(ref="no-such-ref")


@unittest.skipUnless(shutil.which("git"), "git not installed")
class HistoryCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.repo = Path(self._tmp)
        git(self.repo, "init", "-q")
        (self.repo / "out").mkdir()
        for i, body in enumerate((dump(a=1, b=2), dump(a=1, b=9), "{ not json", dump(a=5, b=9)), 1):
            (self.repo / PATH).write_text(body, encoding="utf-8")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", f"commit {i}")

    def tearDown(self):
        rmtree_force(self._tmp)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(["history", "--repo", str(self.repo), "-p", PATH, *argv])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_text_output_and_a_broken_commit_in_the_middle(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("skipped", err)  # "{ not json" can't be parsed
        self.assertIn("commit 4", out)
        self.assertIn("commit 2", out)
        self.assertIn("commit 1", out)
        self.assertNotIn("commit 3", out)
        # commit 4 is compared with commit 2, the last good state
        self.assertIn("~ client.a  0x1 -> 0x5  (+0x4)", out)
        self.assertEqual(out.count("\n\n"), 2)  # entries are separated by a blank line

    def test_json_lines(self):
        code, out, _ = self.run_cli("-f", "json", "-i", "client.b")
        lines = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([x["subject"] for x in lines], ["commit 2", "commit 1"])
        self.assertEqual(lines[0]["changed"][0]["delta"], 7)

    def test_arguments(self):
        self.assertEqual(self.run_cli("--limit", "-1")[0], 2)
        self.assertEqual(self.run_cli("--ref", "nope")[0], 2)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli("-g")[0], 2)  # no layout shifts here: argparse exits
        code, _, err = self.run_cli("-i", "zzz-never")
        self.assertEqual(code, 0)
        self.assertIn("no commit in range", err)

    def test_parser_has_no_group_flag(self):
        self.assertNotIn("--group", build_parser().format_help())


if __name__ == "__main__":
    unittest.main()
