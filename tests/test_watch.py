import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from offset_diff.cli import main
from offset_diff.gitsource import resolve
from offset_diff.watch import build_parser, poll, run

PATH = "out/offsets.json"


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def rmtree_force(path: str) -> None:
    # git object files are read-only on Windows
    shutil.rmtree(path, onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))


def offsets(**kv: int) -> str:
    return json.dumps({"c": kv})


@unittest.skipUnless(shutil.which("git"), "git not installed")
class WatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        base = Path(self._tmp)
        self.origin, self.author, self.watcher = base / "origin.git", base / "author", base / "watcher"
        self.state = base / "state.txt"
        git(base, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(base, "init", "-q", "-b", "main", str(self.author))
        git(self.author, "remote", "add", "origin", str(self.origin))
        self.first = self.commit({PATH: offsets(a=1, b=2)}, "build 1")
        git(self.author, "push", "-q", "-u", "origin", "main")
        git(base, "clone", "-q", str(self.origin), str(self.watcher))

    def tearDown(self):
        rmtree_force(self._tmp)

    def commit(self, files: dict[str, str | None], message: str) -> str:
        for rel, text in files.items():
            target = self.author / rel
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git(self.author, "add", "-A")
        git(self.author, "commit", "-q", "-m", message)
        return git(self.author, "rev-parse", "HEAD")

    def push(self, files: dict[str, str | None], message: str) -> str:
        sha = self.commit(files, message)
        git(self.author, "push", "-q", "origin", "main")
        return sha

    def watch(self, *extra: str, sleep=None):
        argv = ["--repo", str(self.watcher), "-p", PATH, "--state", str(self.state), *extra]
        if sleep is None:
            argv.append("--once")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(build_parser().parse_args(argv), **({"sleep": sleep} if sleep else {}))
        return code, out.getvalue(), err.getvalue()

    def test_first_run_sets_baseline_then_reports_new_commits_in_order(self):
        code, out, err = self.watch()
        self.assertEqual((code, out), (0, ""))
        self.assertIn("watching @{u}", err)
        self.assertEqual(self.state.read_text().strip(), resolve(str(self.watcher), "HEAD"))

        second = self.push({PATH: offsets(a=1, b=5)}, "build 2")
        third = self.push({PATH: offsets(a=1, b=5, d=9)}, "build 3")
        code, out, _ = self.watch()
        self.assertEqual(code, 1)
        self.assertLess(out.index("build 2"), out.index("build 3"))
        self.assertIn(f"commit {second[:8]}  build 2", out)
        self.assertIn(f"{self.first[:8]}:{PATH}", out)  # first diff is against the baseline
        self.assertIn(f"{second[:8]}:{PATH}", out)  # second diff is against build 2, not build 1
        self.assertIn("c.b", out)
        self.assertIn("ADDED", out)
        self.assertEqual(self.state.read_text().strip(), third)

        self.assertEqual(self.watch()[:2], (0, ""))  # nothing new, nothing printed

    def test_commits_that_move_no_offsets_are_silent(self):
        self.watch()
        self.push({PATH: json.dumps({"c": {"a": 1, "b": 2}}, indent=4)}, "reformat")
        self.push({"README.md": "hello"}, "docs")
        self.assertEqual(self.watch()[:2], (0, ""))

    def test_since_reports_history_on_first_run(self):
        self.push({PATH: offsets(a=1, b=7)}, "build 2")
        code, out, _ = self.watch("--since", self.first)
        self.assertEqual(code, 1)
        self.assertIn("build 2", out)

    def test_state_wins_over_since(self):
        self.watch()
        self.push({PATH: offsets(a=1, b=7)}, "build 2")
        self.watch()
        self.assertEqual(self.watch("--since", self.first)[:2], (0, ""))

    def test_json_lines_and_out_dir(self):
        self.watch()
        sha = self.push({PATH: offsets(a=1, b=9)}, "build 2")
        reports = Path(self._tmp) / "reports"
        code, out, _ = self.watch("-f", "json", "--out-dir", str(reports))
        (line,) = out.splitlines()
        data = json.loads(line)
        self.assertEqual((data["commit"], data["subject"]), (sha, "build 2"))
        self.assertEqual(data["changed"][0]["key"], "c.b")
        self.assertEqual(json.loads((reports / f"{sha[:12]}.json").read_text().strip()), data)

    def test_file_appearing_and_disappearing(self):
        self.watch()
        self.push({PATH: None}, "drop dump")
        code, out, _ = self.watch()
        self.assertIn("REMOVED", out)
        self.push({PATH: offsets(z=4)}, "dump is back")
        code, out, _ = self.watch()
        self.assertEqual(code, 1)
        self.assertIn("ADDED", out)
        self.assertIn("c.z", out)

    def test_unparseable_commit_is_skipped_and_next_compares_with_last_good(self):
        self.watch()
        self.push({PATH: "{ not json"}, "broken")
        good = self.push({PATH: offsets(a=1, b=6)}, "fixed")
        code, out, err = self.watch()
        self.assertEqual(code, 1)
        self.assertIn("fixed", out)
        self.assertNotIn("broken", out)
        self.assertIn("skipped", err)
        self.assertIn(f"{self.first[:8]}:{PATH}", out)  # compared with the last good state
        self.assertEqual(self.state.read_text().strip(), good)

    def test_forgotten_baseline_restarts_from_head(self):
        self.state.write_text("0" * 40 + "\n")
        code, out, err = self.watch()
        self.assertEqual((code, out), (0, ""))
        self.assertIn("no longer exists", err)
        self.assertEqual(self.state.read_text().strip(), resolve(str(self.watcher), "HEAD"))

    def test_include_and_min_delta_filters_apply(self):
        self.watch()
        self.push({PATH: offsets(a=1, b=2, c=8)}, "tiny")
        self.assertEqual(self.watch("-i", "nomatch")[:2], (0, ""))
        self.push({PATH: offsets(a=2, b=2, c=8)}, "a moved by 1")
        self.assertEqual(self.watch("--min-delta", "4")[:2], (0, ""))

    def test_no_fetch_and_fetch_failure(self):
        self.watch()
        self.push({PATH: offsets(a=1, b=5)}, "build 2")
        self.assertEqual(self.watch("--no-fetch")[:2], (0, ""))  # not fetched, so not seen
        git(self.watcher, "remote", "set-url", "origin", str(Path(self._tmp) / "gone.git"))
        code, out, err = self.watch()
        self.assertEqual((code, out), (0, ""))
        self.assertIn("fetch failed", err)

    def test_loop_polls_until_interrupted(self):
        self.watch()
        calls = []

        def fake_sleep(seconds):
            calls.append(seconds)
            if len(calls) == 1:
                self.push({PATH: offsets(a=1, b=11)}, "while sleeping")
            else:
                raise KeyboardInterrupt

        out, err = io.StringIO(), io.StringIO()
        argv = ["--repo", str(self.watcher), "-p", PATH, "--state", str(self.state), "--interval", "42"]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(KeyboardInterrupt):
                run(build_parser().parse_args(argv), sleep=fake_sleep)
        self.assertEqual(calls, [42.0, 42.0])
        self.assertIn("while sleeping", out.getvalue())

    def test_poll_returns_updates_without_touching_state(self):
        base = resolve(str(self.watcher), "HEAD")
        self.push({PATH: offsets(a=1, b=5)}, "build 2")
        git(self.watcher, "fetch", "-q")
        head, updates, warnings = poll(str(self.watcher), "@{u}", PATH, base)
        self.assertEqual([u.subject for u in updates], ["build 2"])
        self.assertEqual(updates[0].base, base)
        self.assertEqual((head, warnings), (updates[0].commit, []))
        self.assertEqual(poll(str(self.watcher), "@{u}", PATH, head), (head, [], []))


@unittest.skipUnless(shutil.which("git"), "git not installed")
class WatchCliTests(unittest.TestCase):
    def test_dispatch_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(
                    main(["watch", "--repo", tmp, "-p", PATH, "--once", "--no-fetch"]), 2
                )  # not a repo
            self.assertIn("offset-diff watch:", err.getvalue())

    def test_required_and_invalid_arguments(self):
        for argv in (["watch"], ["watch", "--repo", "."], ["watch", "--repo", ".", "-p", "x", "--interval", "0"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                main(argv)

    def test_plain_diff_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
            a.write_text('{"x": 1}')
            b.write_text('{"x": 2}')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(a), str(b), "--fail-on-change"]), 1)


if __name__ == "__main__":
    unittest.main()
