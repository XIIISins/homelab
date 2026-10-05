"""Phase 10h2: the author dispatcher and the drafting session runner (aiops/author/{dispatcher,session,toolcli}.py).

A REAL local bare git repo is the "origin" (so clone, apply, commit and push run for real); GitHub, the Toolbelt and the
Claude session are fakes. The questions: does a bad patch never reach a push, is the token kept out of the session, does the
admin-token guard stop everything, are failures always reported.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
for sub in ("author", "toolbelt", "tools"):
    sys.path.insert(0, str(REPO / "aiops" / sub))
import dispatcher  # noqa: E402
import scope  # noqa: E402
import session  # noqa: E402
import toolcli  # noqa: E402

CLASSES = scope.load_classes((REPO / "aiops" / "author-classes.yml").read_text())
GIT_ENV = {"PATH": os.environ["PATH"], "HOME": "/nonexistent", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}


def git(*args, cwd=None):
    return subprocess.run(["git", "-c", "init.defaultBranch=main", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True).stdout


def make_origin(tmp: Path) -> str:
    seed = tmp / "seed"
    seed.mkdir()
    git("init", "-q", cwd=seed)
    for p, t in (("docs/incidents/README.md", "# incidents\n"), ("docs/known-issues/a.md", "# a\n"), ("CLAUDE.md", "rules\n"), ("aiops/actions.yml", "x: 1\n")):
        (seed / p).parent.mkdir(parents=True, exist_ok=True)
        (seed / p).write_text(t)
    (seed / ".github/scripts").mkdir(parents=True)
    (seed / ".github/scripts/ci-doc-links.py").write_text((REPO / ".github/scripts/ci-doc-links.py").read_text())
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "seed", cwd=seed)
    bare = tmp / "origin.git"
    git("clone", "-q", "--bare", str(seed), str(bare))
    return f"file://{bare}"


def patch_of(url: str, edits: dict, tmp: Path) -> str:
    w = tmp / "w"
    git("clone", "-q", url, str(w))
    for p, t in edits.items():
        (w / p).parent.mkdir(parents=True, exist_ok=True)
        (w / p).write_text(t)
    git("add", "-A", cwd=w)
    out = git("diff", "--cached", "--binary", "--full-index", cwd=w)
    import shutil
    shutil.rmtree(w)
    return out


class FakeTB:
    def __init__(self, prs=None):
        self.reports, self.claims, self.prs = [], [], prs or []

    def report(self, cid, **f):
        self.reports.append((cid, f))
        return 200, {}

    def claim(self):
        return self.claims.pop(0) if self.claims else {"change_request": None, "why": "none-approved"}

    def open_prs(self):
        return self.prs


class FakeGH:
    def __init__(self, admin=False, states=None, pr_status=201):
        self.ident = {"login": "bot", "ok": True, "admin": admin, "maintain": False, "push": True}
        self.created, self.labels, self.states, self.pr_status = [], [], states or {}, pr_status

    def identity(self):
        return self.ident

    def create_pr(self, head, base, title, body):
        self.created.append((head, base, title, body))
        return self.pr_status, {"number": 200, "html_url": "https://github.com/XIIISins/homelab/pull/200"}

    def label(self, n, label):
        self.labels.append((n, label))

    def pr_state(self, n):
        return self.states.get(n, "open")


CR = {"id": 7, "class": "docs", "title": "Draft the NVMe latency note", "body": "write it", "source": "operator", "source_ref": "",
      "decided_by": "111", "allowed_paths": ["docs/incidents/**", "docs/known-issues/**"], "state": "running"}


class Rig(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.url = make_origin(self.tmp)
        self.work = self.tmp / "work"
        (self.work / "jobs").mkdir(parents=True)
        self.cfg = dispatcher.Config(repo="XIIISins/homelab", repo_url=self.url, work=str(self.work), classes=CLASSES, tools_url="http://x", tools_cli="/c",
                                     agent_file="/a.md")
        self.tb, self.gh = FakeTB(), FakeGH()
        self.askpass = self.tmp / "askpass.sh"
        self.askpass.write_text("#!/bin/sh\necho x\n")
        self.askpass.chmod(0o755)

    def tearDown(self):
        self._t.cleanup()

    def disp(self, patch=None, result=None, summary="Wrote the note.", gh=None, cfg=None):
        gh = gh or self.gh

        def fake_session(cid):
            out = self.work / "jobs" / str(cid) / "out"
            res = result if result is not None else {"ok": True, "turns": 5, "cost_usd": 0.3}
            (out / "result.json").write_text(json.dumps(res))
            if patch is not None:
                (out / "change.patch").write_text(patch)
            if summary:
                (out / "summary.md").write_text(summary)
            self.assertTrue((self.work / "jobs" / str(cid) / "env").exists())  # the key is there only while the session runs
            return 0

        return dispatcher.Dispatcher(cfg or self.cfg, self.tb, gh, dispatcher.Git("pat-not-real", str(self.askpass)),
                                     {"anthropic": "k" * 20, "tools": "t" * 20}, start_session=fake_session)

    def branches(self):
        return git("ls-remote", "--heads", self.url).strip()

    def last_report(self):
        return self.tb.reports[-1][1]


class Publish(Rig):
    def test_a_good_docs_patch_is_pushed_as_the_bot_and_a_pr_is_opened(self):
        patch = patch_of(self.url, {"docs/incidents/2026-10-04-nvme.md": "# NVMe\n\nbody\n"}, self.tmp)
        out = self.disp(patch).process(dict(CR))
        self.assertEqual(out["state"], "pr-open")
        self.assertIn("refs/heads/agent/docs/7-draft-the-nvme-latency-note", self.branches())
        head, base, title, body = self.gh.created[0]
        self.assertEqual((head, base), ("agent/docs/7-draft-the-nvme-latency-note", "main"))
        self.assertTrue(title.startswith("docs: "))
        self.assertIn("change request **#7**", body)
        self.assertEqual(self.gh.labels, [(200, "agent-authored")])
        rep = self.last_report()
        self.assertEqual((rep["state"], rep["pr_url"]), ("pr-open", "https://github.com/XIIISins/homelab/pull/200"))
        c = git("--git-dir", self.url[7:], "log", "-1", "--format=%an|%s", "agent/docs/7-draft-the-nvme-latency-note").strip()
        self.assertTrue(c.startswith("aiops-author|docs: Draft the NVMe"))
        self.assertFalse((self.work / "jobs" / "7" / "env").exists())
        self.assertFalse((self.work / "jobs" / "7" / "repo").exists())

    def test_the_class_checks_run_on_the_patched_clone_and_their_results_reach_the_pr_and_the_report(self):
        patch = patch_of(self.url, {"docs/incidents/2026-10-04-ok.md": "# ok\n\nsee [the index](README.md)\n"}, self.tmp)
        self.assertEqual(self.disp(patch).process(dict(CR))["state"], "pr-open")
        body = self.gh.created[0][3]
        self.assertIn("- doc links: pass", body)
        self.assertIn("- scope rules: pass", body)
        tests = self.last_report()["tests"]
        self.assertTrue(tests["doc links"].startswith("pass"))
        self.assertEqual(tests["secret scan"], "pass")

    def test_a_failing_check_blocks_the_push(self):
        patch = patch_of(self.url, {"docs/incidents/2026-10-04-bad.md": "# bad\n\nsee [gone](no-such-file.md)\n"}, self.tmp)
        out = self.disp(patch).process(dict(CR))
        self.assertEqual(out["state"], "failed")
        self.assertIn("a check failed", self.last_report()["error"])
        self.assertIn("doc links", self.last_report()["error"])
        self.assertEqual(self.branches(), git("ls-remote", "--heads", self.url).strip())
        self.assertNotIn("agent/docs", self.branches())
        self.assertEqual(self.gh.created, [])

    def test_a_check_script_outside_github_scripts_never_runs(self):
        r = dispatcher.run_checks(str(self.tmp), [{"name": "x", "script": "../evil.py"}, {"name": "y", "script": "docs/a.py"}])
        self.assertTrue(all(v.startswith("fail") for v in r.values()), r)

    def test_a_pr_refusal_is_reported_after_the_push(self):
        patch = patch_of(self.url, {"docs/incidents/x.md": "x\n"}, self.tmp)
        out = self.disp(patch, gh=FakeGH(pr_status=422)).process(dict(CR))
        self.assertEqual(out["state"], "failed")
        self.assertIn("HTTP 422", self.last_report()["error"])


class Refusals(Rig):
    def assert_refused(self, patch, needle, **kw):
        out = self.disp(patch, **kw).process(dict(CR))
        self.assertEqual(out["state"], "failed", out)
        self.assertIn(needle, self.last_report()["error"])
        self.assertEqual(self.branches().count("agent/"), 0)  # nothing was pushed
        self.assertEqual(self.gh.created, [])

    def test_forbidden_and_out_of_class_paths(self):
        self.assert_refused(patch_of(self.url, {"CLAUDE.md": "edited\n"}, self.tmp), "forbidden for agents")

    def test_a_path_outside_the_requests_own_list(self):
        self.assert_refused(patch_of(self.url, {"docs/procedures/p.md": "p\n"}, self.tmp), "not in the change request's allowed paths")

    def test_secret_shaped_added_lines(self):
        fake = "gh" + "p_" + "A" * 36  # runtime-built: the repo's secret scan blocks literal fixtures
        self.assert_refused(patch_of(self.url, {"docs/incidents/x.md": f"the key was {fake}\n"}, self.tmp), "looks like a secret")
        fake2 = "pass" + "word=" + "Zz9" * 6
        self.assert_refused(patch_of(self.url, {"docs/incidents/y.md": f"{fake2}\n"}, self.tmp), "looks like a secret")

    def test_binary_symlink_and_mode_changes(self):
        for text, needle in (("diff --git a/docs/incidents/a.bin b/docs/incidents/a.bin\nnew file mode 100644\nindex 0..1\nGIT binary patch\nliteral 1\n", "binary"),
                             ("diff --git a/docs/incidents/l b/docs/incidents/l\nnew file mode 120000\nindex 0..1\n", "only regular"),
                             ("diff --git a/docs/incidents/s.sh b/docs/incidents/s.sh\nnew file mode 100755\nindex 0..1\n", "only regular"),
                             ("diff --git a/docs/incidents/README.md b/docs/incidents/README.md\nold mode 100644\nnew mode 100755\n", "mode changes")):
            self.tb.reports.clear()
            out = self.disp(text).process(dict(CR))
            self.assertEqual(out["state"], "failed")
            self.assertIn(needle, self.last_report()["error"])

    def test_a_failed_or_empty_session_is_reported_not_pushed(self):
        self.assert_refused("", "no change", result={"ok": False, "error": "the session produced no change"})
        self.tb.reports.clear()
        out = self.disp("", result={"ok": False, "error": "the session ran past its wall clock"}).process(dict(CR))
        self.assertIn("wall clock", self.last_report()["error"])

    def test_the_admin_token_guard(self):
        patch = patch_of(self.url, {"docs/incidents/x.md": "x\n"}, self.tmp)
        self.assert_refused(patch, "identity check", gh=FakeGH(admin=True))
        cfg = dispatcher.Config(repo_url=self.url, work=str(self.work), classes=CLASSES, allow_admin_token=True)
        self.tb.reports.clear()
        self.assertEqual(self.disp(patch, gh=FakeGH(admin=True), cfg=cfg).process(dict(CR))["state"], "pr-open")

    def test_dry_run_checks_everything_and_pushes_nothing(self):
        cfg = dispatcher.Config(repo_url=self.url, work=str(self.work), classes=CLASSES, dry_run=True)
        self.assert_refused(patch_of(self.url, {"docs/incidents/x.md": "x\n"}, self.tmp), "dry run", cfg=cfg)


class Launch(Rig):
    def test_the_session_is_started_by_a_marker_and_awaited_by_its_result_file(self):
        import threading
        import time
        d = self.disp()
        job = self.work / "jobs" / "7"
        (job / "out").mkdir(parents=True)

        def launcher():  # stands in for the root launcher + the session unit
            for _ in range(100):
                if (self.work / "markers" / "7.ready").exists():
                    (job / "out" / "result.json").write_text("{}")
                    return
                time.sleep(0.02)
        t = threading.Thread(target=launcher)
        t.start()
        self.assertEqual(d._systemd_session(7, poll=0.02), 0)
        t.join()

    def test_a_session_that_never_answers_gets_a_stop_marker(self):
        cfg = dispatcher.Config(repo_url=self.url, work=str(self.work), classes=CLASSES, session_timeout=-119)
        d = self.disp(cfg=cfg)
        job = self.work / "jobs" / "8"
        (job / "out").mkdir(parents=True)
        self.assertEqual(d._systemd_session(8, poll=0.01), 124)
        self.assertTrue((self.work / "markers" / "8.stop").exists())


class LauncherScript(unittest.TestCase):
    """ansible/roles/aiops-author/files/aiops-draft-launch.py: the root launcher acts only on `<digits>.ready|stop` regular files in
    the markers directory whose job directory is real, and clears everything else so a stray entry cannot re-trigger the path unit."""

    def setUp(self):
        import importlib.util
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        (self.root / "jobs").mkdir()
        (self.root / "markers").mkdir()
        spec = importlib.util.spec_from_file_location("launch_under_test", REPO / "ansible/roles/aiops-author/files/aiops-draft-launch.py")
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.m.JOBS, self.m.MARKERS = str(self.root / "jobs"), str(self.root / "markers")
        self.calls = []
        self.m.subprocess = mock.Mock(run=lambda argv, **kw: self.calls.append(argv))
        self.m.log = lambda msg: None

    def job(self, n):
        (self.root / "jobs" / str(n)).mkdir()

    def marker(self, name, text=""):
        (self.root / "markers" / name).write_text(text)

    def left(self):
        return sorted(p.name for p in (self.root / "markers").iterdir())

    def test_a_ready_marker_starts_the_unit_and_is_consumed(self):
        self.job(7)
        self.marker("7.ready")
        self.m.main()
        self.assertEqual(self.calls, [["/usr/bin/systemctl", "reset-failed", "aiops-draft@7.service"],
                                      ["/usr/bin/systemctl", "start", "--no-block", "aiops-draft@7.service"]])
        self.assertEqual(self.left(), [])

    def test_stop_comes_before_start_for_the_same_job(self):
        self.job(9)
        self.marker("9.ready")
        self.marker("9.stop")
        self.m.main()
        self.assertEqual([c[1] for c in self.calls], ["stop", "reset-failed", "start"])

    def test_no_real_job_directory_no_action_and_the_marker_is_still_cleared(self):
        self.marker("5.ready")
        (self.root / "elsewhere").mkdir()
        (self.root / "jobs" / "6").symlink_to(self.root / "elsewhere")  # a symlinked job directory is not a job
        self.marker("6.ready")
        self.m.main()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.left(), [])

    def test_junk_oversize_and_symlinked_markers_never_act_and_never_linger(self):
        self.job(3)
        target = self.root / "victim.txt"
        target.write_text("keep me")
        (self.root / "markers" / "3.ready").symlink_to(target)
        self.marker("3.stop", "x" * 2000)
        for bad in ("3.ready.bak", "abc", "1234567890.ready", "-1.ready", ".hidden"):
            self.marker(bad)
        (self.root / "markers" / "somedir").mkdir()
        self.m.main()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.left(), [])
        self.assertEqual(target.read_text(), "keep me")  # the symlink was removed, never followed

    def test_the_path_unit_watches_the_fixed_directory_not_a_glob(self):
        unit = (REPO / "ansible/roles/aiops-author/templates/aiops-draft-launch.path.j2").read_text()
        self.assertIn("DirectoryNotEmpty={{ aiops_author_work }}/markers", unit)
        self.assertNotIn("PathExistsGlob", unit.replace("# ", "").split("[Path]")[1])


class ClaimBlocked(Rig):
    def test_a_refused_claim_leaves_a_journal_line_once_per_reason_per_ten_minutes(self):
        seen = []
        orig = dispatcher.audit
        dispatcher.audit = lambda event, **kw: seen.append((event, kw))
        try:
            self.tb.claim = lambda: {"change_request": None, "why": "daily-budget", "detail": "the daily budget is used up (6 of 6)"}
            d = self.disp()
            for _ in range(3):
                d.tick(set())
            self.assertEqual([e for e in seen if e[0] == "claim_blocked"], [("claim_blocked", {"why": "daily-budget", "detail": "the daily budget is used up (6 of 6)"})])
            self.tb.claim = lambda: {"change_request": None, "why": "none-approved"}  # an empty queue is not news
            seen.clear()
            d.tick(set())
            self.assertEqual([e for e in seen if e[0] == "claim_blocked"], [])
            self.tb.claim = lambda: {"change_request": None, "why": "maintenance", "detail": "maintenance mode is on"}  # a new reason is
            d.tick(set())
            self.assertEqual([e[1]["why"] for e in seen if e[0] == "claim_blocked"], ["maintenance"])
        finally:
            dispatcher.audit = orig


class Loop(Rig):
    def test_reconcile_reports_merged_and_closed_only(self):
        self.tb.prs = [{"id": 1, "pr_url": "https://github.com/XIIISins/homelab/pull/11"}, {"id": 2, "pr_url": "https://github.com/XIIISins/homelab/pull/12"},
                       {"id": 3, "pr_url": "https://github.com/XIIISins/homelab/pull/13"}]
        d = self.disp(gh=FakeGH(states={11: "merged", 12: "closed"}))
        d.reconcile()
        self.assertEqual([(c, f["state"]) for c, f in self.tb.reports], [(1, "merged"), (2, "closed")])

    def test_tick_does_not_claim_with_a_refused_identity_or_a_full_house(self):
        d = self.disp(gh=FakeGH(admin=True))
        self.tb.claims = [{"change_request": dict(CR)}]
        d.tick(set())
        self.assertEqual(len(self.tb.claims), 1)
        d2 = self.disp()
        d2.tick({1, 2})
        self.assertEqual(len(self.tb.claims), 1)
        # a dry run publishes nothing, so an unsafe identity does not stop it from claiming
        dry = dispatcher.Config(repo_url=self.url, work=str(self.work), classes=CLASSES, dry_run=True, max_parallel=1)
        d3 = self.disp(gh=FakeGH(admin=True), cfg=dry)
        d3.process = lambda cr: None
        d3.tick(set())
        self.assertEqual(len(self.tb.claims), 0)


class Parse(unittest.TestCase):
    def test_counts_and_renames(self):
        p = ("diff --git a/docs/a.md b/docs/b.md\nsimilarity index 90%\nrename from docs/a.md\nrename to docs/b.md\nindex 1..2 100644\n--- a/docs/a.md\n+++ b/docs/b.md\n"
             "@@ -1,2 +1,2 @@\n-old\n+new\n same\n")
        f = dispatcher.parse_patch(p)
        self.assertEqual(f, [{"filename": "docs/b.md", "status": "renamed", "previous_filename": "docs/a.md", "additions": 1, "deletions": 1}])

    def test_empty_and_oversize(self):
        with self.assertRaises(dispatcher.PatchError):
            dispatcher.parse_patch("")
        with self.assertRaises(dispatcher.PatchError):
            dispatcher.parse_patch("x" * (600 * 1024))

    def test_a_plus_plus_plus_line_inside_a_hunk_is_content_and_is_scanned(self):
        secret = "pass" + "word=" + "Zz9" * 6
        p = f"diff --git a/d.md b/d.md\nindex 1..2 100644\n--- a/d.md\n+++ b/d.md\n@@ -1 +1,2 @@\n x\n++{secret}\n"
        self.assertEqual(dispatcher.parse_patch(p)[0]["additions"], 1)
        self.assertEqual(dispatcher.added_lines(p), ["+" + secret])
        self.assertTrue(dispatcher.secret_findings(p))

    def test_slug_and_branch_always_match_the_scope_rules(self):
        for title in ("Hello, World!", "###", "x" * 200, "ÄÖ ü"):
            b = dispatcher.branch_for({"class": "docs", "id": 3, "title": title})
            self.assertIsNotNone(scope.branch_class(b), b)


class Session(unittest.TestCase):
    def test_the_session_holds_no_github_token_and_runs_with_a_fixed_toolset(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            url = make_origin(tmp)
            job = tmp / "job"
            (job / "out").mkdir(parents=True)
            agent = tmp / "agent.md"
            agent.write_text("---\nname: aiops-author\n---\nYou draft PRs.\n")
            (job / "spec.json").write_text(json.dumps({"id": 5, "class": "docs", "title": "T", "body": "B", "allowed_paths": ["docs/incidents/**"],
                                                       "limits": {"max_files": 12, "max_changed_lines": 600}, "repo_url": url, "base": "main",
                                                       "tools_url": "http://tb", "tools_cli": "/opt/toolcli.py", "agent_file": str(agent)}))
            seen = {}

            def runner(argv, **kw):
                if argv[0] == "git":
                    kw.pop("env", None) if "-C" in argv else None
                    return subprocess.run(argv, **{**kw, "env": GIT_ENV})
                seen["argv"], seen["env"], seen["cwd"] = argv, kw["env"], kw["cwd"]
                (Path(kw["cwd"]) / "docs" / "incidents" / "n.md").write_text("note\n")
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"num_turns": 4, "total_cost_usd": 0.2}), stderr="")

            env = {"ANTHROPIC_API_KEY": "k" * 20, "AIOPS_TOOLS_TOKEN": "t" * 20, "GITHUB_TOKEN": "must-not-pass", "AIOPS_AUTHOR_PAT": "must-not-pass"}
            res = session.run(job, env=env, runner=runner)
            self.assertTrue(res["ok"], res)
            self.assertEqual(set(seen["env"]), {"PATH", "HOME", "ANTHROPIC_API_KEY", "AIOPS_TOOLS_URL", "AIOPS_TOOLS_TOKEN", "AIOPS_CR_ID", "GIT_TERMINAL_PROMPT"})
            argv = seen["argv"]
            self.assertIn("--bare", argv)
            self.assertIn("WebFetch", argv[argv.index("--disallowedTools"):])
            self.assertNotIn("Bash", argv[argv.index("--allowedTools"):argv.index("--disallowedTools")])  # only the fixed Bash(...) patterns
            self.assertIn("You draft PRs.", argv[argv.index("--append-system-prompt") + 1])
            self.assertNotIn("name: aiops-author", argv[argv.index("--append-system-prompt") + 1])  # frontmatter stripped
            patch = (job / "out" / "change.patch").read_text()
            self.assertEqual([f["filename"] for f in dispatcher.parse_patch(patch)], ["docs/incidents/n.md"])

    def test_a_session_that_changes_nothing_is_not_ok(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            url = make_origin(tmp)
            job = tmp / "job"
            (job / "out").mkdir(parents=True)
            agent = tmp / "a.md"
            agent.write_text("p")
            (job / "spec.json").write_text(json.dumps({"id": 5, "class": "docs", "title": "T", "body": "B", "allowed_paths": ["docs/**"],
                                                       "limits": {"max_files": 1, "max_changed_lines": 1}, "repo_url": url, "tools_url": "u",
                                                       "tools_cli": "/c", "agent_file": str(agent)}))
            run = lambda argv, **kw: (subprocess.run(argv, **{**kw, "env": GIT_ENV}) if argv[0] == "git"
                                      else subprocess.CompletedProcess(argv, 0, stdout="{}", stderr=""))
            res = session.run(job, env={"ANTHROPIC_API_KEY": "k", "AIOPS_TOOLS_TOKEN": "t"}, runner=run)
            self.assertFalse(res["ok"])
            self.assertIn("no change", res["error"])
            self.assertEqual(session.exit_code(res), 0)  # declining is an answer in result.json, not a failed unit

    def test_the_unit_fails_only_for_a_real_failure(self):
        ok = {"ok": True}
        declined = {"ok": False, "error": session.NO_CHANGE, "claude_exit": 0, "claude_error": False}
        self.assertEqual(session.exit_code(ok), 0)
        self.assertEqual(session.exit_code(declined), 0)
        self.assertEqual(session.exit_code({**declined, "claude_error": True}), 1)  # Claude itself errored and left no patch
        self.assertEqual(session.exit_code({**declined, "claude_exit": 1}), 1)
        self.assertEqual(session.exit_code({"ok": False, "error": "the session ran past its wall clock"}), 1)
        self.assertEqual(session.exit_code({"ok": False, "error": "session setup failed: OSError"}), 1)


class ToolCli(unittest.TestCase):
    def test_usage_and_environment_errors(self):
        self.assertEqual(toolcli.main(["toolcli.py"]), 2)
        self.assertEqual(toolcli.main(["toolcli.py", "bad name"]), 2)
        os.environ.pop("AIOPS_TOOLS_URL", None)
        self.assertEqual(toolcli.main(["toolcli.py", "git.log", "{}"]), 2)

    def test_the_request_id_is_sent_and_required(self):
        sent = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_open(req, timeout=0):
            sent["body"] = json.loads(req.data)
            return Resp()

        env = {"AIOPS_TOOLS_URL": "http://tb:8090", "AIOPS_TOOLS_TOKEN": "t" * 20}
        with mock.patch.dict(os.environ, {**env, "AIOPS_CR_ID": "7"}), mock.patch.object(toolcli.urllib.request, "urlopen", fake_open):
            self.assertEqual(toolcli.main(["toolcli.py", "registry.actions", "{}"]), 0)
        self.assertEqual(sent["body"], {"args": {}, "change_request_id": 7})
        with mock.patch.dict(os.environ, env), mock.patch.object(toolcli.urllib.request, "urlopen", fake_open):
            os.environ.pop("AIOPS_CR_ID", None)
            self.assertEqual(toolcli.main(["toolcli.py", "registry.actions", "{}"]), 2)


if __name__ == "__main__":
    unittest.main()
