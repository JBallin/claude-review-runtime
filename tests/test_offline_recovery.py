"""Bounded offline restoration/restart schedules with durable synthetic API state.

Each worker imports the real helper afresh. Only the API boundary is replaced;
no GitHub requests, model execution, or production code changes are involved.

These finite schedules do not prove atomicity between separate ownership reads
and writes, runner cancellation/queue delivery, artifact transport, real API
outages, or sustained load. Those platform acceptance gaps remain open.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_claude_review_check import (
    check, HEAD, OTHER, BASE_TIP, MERGE_BASE, REPO, check_run, comment,
    evidence_of, reaction, presentation_owner,
)
from test_presentation_ownership import PresentationAPI


class DurableAPI(PresentationAPI):
    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        self.__dict__.update(json.loads(self.path.read_text()))

    def save(self):
        self.path.write_text(json.dumps({key: value for key, value in self.__dict__.items()
                                        if key not in ("path", "on_call")}))

    def call(self, args, body=None):
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        path = next(arg for arg in args if arg.startswith("repos/")).split("?")[0]
        special = ("/check-runs" in path or path == f"repos/{REPO}/pulls/7/comments"
                   or (method == "POST" and path.endswith("/issues/7/comments")
                       and check.STATUS_MARKER not in body["body"]))
        try:
            if self.cut == [method, path] and self.cut_before:
                self.cut = None
                self.save()
                os._exit(73)
            if not special:
                if (getattr(self, "presentation_outage", False) and method == "GET"
                        and path == f"repos/{REPO}/issues/7/comments"):
                    self.calls.append((method, path, copy.deepcopy(body)))
                    raise check.GhError("synthetic presentation outage")
                result = super().call(args, body)
            else:
                self.calls.append((method, path, copy.deepcopy(body)))
                if self.outage and method == "GET" and "/check-runs" in path:
                    raise check.GhError("synthetic history outage")
                if "/check-runs" in path:
                    if method == "GET":
                        head = path.split("/commits/", 1)[1].split("/", 1)[0]
                        runs = [run for run in self.checks if run["head_sha"] == head]
                        if self.drift:
                            self.drift = False
                            result = json.dumps({"total_count": len(runs) + 1, "check_runs": runs})
                        else:
                            result = json.dumps({"total_count": len(runs), "check_runs": runs})
                    elif method == "PATCH":
                        run = next(run for run in self.checks if str(run["id"]) == path.rsplit("/", 1)[1])
                        run.update(body)
                        result = json.dumps(run)
                    else:
                        raise AssertionError((method, path))
                elif path.endswith("/pulls/7/comments"):
                    result = json.dumps(self.findings)
                else:
                    notice = {"id": 88, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"}, **body}
                    self.comment_pages.append(notice)
                    result = json.dumps(notice)
            if self.cut == [method, path]:
                self.cut = None
                self.save()
                os._exit(73)  # Process dies after durable commit, before receipt.
            return result
        finally:
            self.save()


def worker(path, command):
    api = DurableAPI(path)
    if command == "deadline":
        # Exercise the actual gh_api timeout gate with a synthetic clock, so
        # this check cannot spend 120 seconds or invoke a real subprocess.
        with mock.patch.object(check.time, "monotonic", return_value=121), \
             mock.patch.object(check, "PRESENTATION_DEADLINE", 120), \
             mock.patch.object(check.subprocess, "run") as run:
            try:
                check.gh_api([f"repos/{REPO}/issues/7/reactions"])
            except check.GhError:
                run.assert_not_called()
                return 0
        return 1
    with mock.patch.object(check, "gh_api", side_effect=api.call):
        if command == "start":
            check.best_effort_status(os.environ["HEAD_SHA"], "in_progress", acquire=True)
            api.save()
            return 0
        if command == "finish":
            if os.environ.get("TEST_COMPLETION_TIME"):
                with mock.patch.object(check, "now", return_value=os.environ["TEST_COMPLETION_TIME"]):
                    return check.cmd_finalize()
            return check.cmd_finalize()
        if command == "probe":
            projection = check.live_pr_projection(REPO, "7")
            print(json.dumps(projection))
            return 0
        if command == "stale":
            return check.cmd_stale()
        raise AssertionError(command)


class OfflineRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "api.json"
        self.before = Path(self.tmp.name) / "before.json"
        self.before.write_text("[]")
        api = PresentationAPI()
        state = {key: value for key, value in api.__dict__.items() if key != "on_call"}
        state.update(checks=[check_run(99, None, status="in_progress")], findings=[],
                     outage=False, drift=False, cut=None, cut_before=False)
        self.path.write_text(json.dumps(state))
        self.env = {**os.environ, "REPO": REPO, "PR_NUMBER": "7", "HEAD_SHA": HEAD,
                    "BASE_REF": "main", "BASE_SHA": BASE_TIP, "MERGE_BASE_SHA": MERGE_BASE,
                    "GITHUB_RUN_ID": "5", "GITHUB_RUN_ATTEMPT": "1", "PRESENTATION_START": "2",
                    "STATUS_COMMENTS_ENABLED": "true", "TRIGGER_KIND": "automatic",
                    "PR_REACTION_MODE": "automatic", "CLAUDE_REVIEW_RETRY_DELAY": "0",
                    "DETAILS_URL": "https://github.com/owner/repo/actions/runs/5",
                    "CHECK_RUN_ID": "99", "REVIEW_RESULT": "success", "ACTION_CONCLUSION": "success",
                    "COMPLETION_VERIFIED": "true", "COMPLETION_REASON": "verified",
                    "FINDING_PUBLICATION": json.dumps({"attempt_count": 0, "comment_ids": []}),
                    "BEFORE_IDS_FILE": str(self.before), "GITHUB_OUTPUT": str(Path(self.tmp.name) / "output")}
        self.env.pop("PRESENTATION_OWNER", None)

    def state(self):
        return json.loads(self.path.read_text())

    def update(self, **changes):
        state = self.state()
        state.update(changes)
        self.path.write_text(json.dumps(state))

    def run_worker(self, command, expected=0, *, require_owner=True):
        if command == "start":
            Path(self.env["GITHUB_OUTPUT"]).write_text("")
        result = subprocess.run([sys.executable, __file__, "--worker", str(self.path), command],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if command == "start" and expected == 0:
            receipts = [line.split("=", 1)[1] for line in Path(self.env["GITHUB_OUTPUT"]).read_text().splitlines()
                        if line.startswith("presentation_owner=")]
            if require_owner:
                self.assertTrue(receipts, "start must export an owner receipt")
            self.env["PRESENTATION_OWNER"] = receipts[-1] if receipts else ""
            outcomes = [line.split("=", 1)[1] for line in Path(self.env["GITHUB_OUTPUT"]).read_text().splitlines()
                        if line.startswith("presentation_outcome=")]
            self.env["PRESENTATION_OUTCOME"] = outcomes[-1] if outcomes else ""
        return result

    def test_stale_start_retains_captured_check_and_finishes_after_exact_base_restoration(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for conclusion in ("success", "action_required"):
                with self.subTest(kind=kind, conclusion=conclusion):
                    self.setUp()
                    self.surface(kind)
                    historical = comment(11, commit=OTHER)
                    unrelated = {"id": 77, "user": {"login": "human"}, "body": "Keep this comment"}
                    self.update(base_tip=OTHER, findings=[historical], comment_pages=[unrelated])
                    self.run_worker("start")
                    owner = json.loads(self.env["PRESENTATION_OWNER"])
                    self.assertEqual(self.env["PRESENTATION_OUTCOME"], "acquired")
                    self.assertEqual(check.status_state(self.state()["status"]), "stale")
                    self.assertEqual((owner["head"], owner["base"]), (HEAD, BASE_TIP))
                    if conclusion == "action_required":
                        self.update(findings=[historical, comment(12)])
                        self.env["FINDING_PUBLICATION"] = json.dumps({"attempt_count": 1, "comment_ids": [12]})
                    # The accepted receipt binds the immutable patch. Restoring
                    # that exact identity follows the established freshness policy.
                    self.update(base_tip=BASE_TIP)
                    for _ in range(2):
                        result = self.run_worker("finish")
                        self.assertNotIn("::warning::", result.stdout)
                    state = self.state()
                    completed = state["checks"][0]
                    self.assertEqual(completed["head_sha"], HEAD)
                    self.assertEqual(completed["conclusion"], conclusion)
                    evidence = evidence_of(completed)
                    self.assertEqual((evidence["reviewed_sha"], evidence["base_sha"], evidence["merge_base_sha"]),
                                     (HEAD, BASE_TIP, MERGE_BASE))
                    self.assertIn(historical, state["findings"])
                    self.assertEqual(state["comment_pages"], [unrelated])
                    self.assertEqual(check.status_state(state["status"]), conclusion)
                    self.assertEqual(check.status_owner(state["status"]), owner)
                    self.assertEqual(len([call for call in state["calls"] if call[0] == "POST"
                                          and call[1].endswith("/comments")]), 1)
                    reactions = state["reactions"][f"repos/{REPO}/issues/7/reactions"]
                    self.assertIn(reaction(701, "heart", login="unrelated-user"), reactions)
                    self.assertEqual([item["content"] for item in reactions
                                      if item["user"]["login"] == check.STATUS_AUTHOR],
                                     ["+1"] if conclusion == "success" else [])

    def test_missing_or_malformed_receipt_preserves_new_owner_and_successful_check(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for receipt in ("", "{", "{}"):
                with self.subTest(kind=kind, receipt=receipt):
                    self.setUp()
                    self.surface(kind)
                    self.run_worker("start")
                    old_env = self.env.copy()
                    self.env.update(GITHUB_RUN_ID="6", PRESENTATION_START="3")
                    self.run_worker("start")
                    previous = copy.deepcopy(self.state()["status"])
                    reactions = copy.deepcopy(self.state()["reactions"])
                    self.env = old_env
                    self.env["PRESENTATION_OWNER"] = receipt
                    for _ in range(2):
                        result = self.run_worker("finish")
                        self.assertIn("::warning::", result.stdout)
                        self.assertNotIn("Expecting value", result.stdout)
                    state = self.state()
                    self.assertEqual(state["checks"][0]["conclusion"], "success")
                    self.assertEqual(state["status"], previous)
                    self.assertEqual(state["reactions"], reactions)

    def test_unavailable_start_does_not_acquire_when_api_recovers(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            with self.subTest(kind=kind):
                self.setUp()
                self.surface(kind)
                self.update(presentation_outage=True,
                            comment_pages=[{"id": 77, "user": {"login": "human"}, "body": "Keep this comment"}])
                start = self.run_worker("start", require_owner=False)
                self.assertEqual(self.env["PRESENTATION_OUTCOME"], "unavailable")
                self.assertIn("::warning::", start.stdout)
                self.update(presentation_outage=False)
                for _ in range(2):
                    result = self.run_worker("finish")
                    self.assertIn("no owner receipt from start (unavailable)", result.stdout)
                self.assertEqual(self.state()["checks"][0]["conclusion"], "success")
                self.assertIsNone(self.state()["status"])

    def surface(self, kind):
        self.env.update(TRIGGER_KIND=kind, PR_REACTION_MODE="automatic" if kind == "automatic" else "manual")
        if kind != "automatic":
            namespace = "issues" if kind == "issue_comment" else "pulls"
            path = f"repos/{REPO}/{namespace}/comments/42"
            self.update(targets={path: {"id": 42, "issue_url" if namespace == "issues" else "pull_request_url":
                                      f"https://api.github.com/repos/{REPO}/{namespace}/7"}})
            self.env["TRIGGER_COMMENT_ID"] = "42"
        opening = f"repos/{REPO}/issues/7/reactions"
        reactions = self.state()["reactions"]
        reactions[opening] = [reaction(701, "heart", login="unrelated-user")]
        self.update(reactions=reactions)

    def test_true_head_and_base_restoration_with_and_without_new_owner(self):
        for field in ("head", "base"):
            for newer in (False, True):
                for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
                    with self.subTest(field=field, newer=newer, kind=kind):
                        self.setUp()
                        self.surface(kind)
                        self.run_worker("start")
                        old_env = self.env.copy()
                        pr = self.state()["pr"]
                        pr[field]["sha"] = OTHER
                        self.update(pr=pr, base_tip=OTHER if field == "base" else BASE_TIP)
                        # A real different captured tuple is observed by a separate process.
                        probe = self.run_worker("probe")
                        self.assertIn(OTHER, probe.stdout)
                        moved = copy.deepcopy(self.state())
                        if newer:
                            self.env.update(GITHUB_RUN_ID="6", PRESENTATION_START="3")
                            self.env["HEAD_SHA" if field == "head" else "BASE_SHA"] = OTHER
                            new_check = check_run(100, None, status="in_progress")
                            new_check["head_sha"] = self.env["HEAD_SHA"]
                            self.update(checks=[*self.state()["checks"], new_check])
                            self.env["CHECK_RUN_ID"] = "100"
                            self.run_worker("start")
                        pr[field]["sha"] = HEAD if field == "head" else BASE_TIP
                        self.update(pr=pr, base_tip=BASE_TIP)
                        restored = copy.deepcopy(self.state()["status"])
                        self.env = old_env
                        self.run_worker("finish")
                        state = self.state()
                        self.assertNotEqual(moved["pr"], state["pr"])
                        self.assertEqual(state["checks"][0]["head_sha"], HEAD)
                        self.assertEqual(state["checks"][0]["conclusion"], "success")
                        if newer:
                            self.assertEqual(state["status"], restored)
                            self.assertEqual(state["checks"][1], new_check)
                        else:
                            self.assertEqual(check.status_state(state["status"]), "success")
                        self.assertIn(reaction(701, "heart", login="unrelated-user"),
                                      state["reactions"][f"repos/{REPO}/issues/7/reactions"])

    def test_restart_after_durable_check_status_and_notice_commits(self):
        for cut, before in ((cut, before) for cut in (
                ("PATCH", f"repos/{REPO}/check-runs/99"),
                ("PATCH", f"repos/{REPO}/issues/comments/55"),
                ("POST", f"repos/{REPO}/issues/7/comments")) for before in (False, True)):
            with self.subTest(cut=cut, before=before):
                self.setUp()
                self.surface("issue_comment")
                self.run_worker("start")
                self.env["MANUAL_COMPLETION_ENABLED"] = "true"
                self.env["TEST_COMPLETION_TIME"] = "2026-10-07T13:09:19Z"
                self.update(cut=list(cut), cut_before=before)
                self.run_worker("finish", 73)
                committed_time = self.state()["checks"][0].get("completed_at")
                self.env["TEST_COMPLETION_TIME"] = "2026-10-07T14:09:19Z"
                self.run_worker("finish")
                expected_time = committed_time or self.env["TEST_COMPLETION_TIME"]
                completed = self.state()["checks"][0]
                self.assertEqual(completed["completed_at"], expected_time)
                self.assertEqual(check.last_completed_review_details(self.state()["status"])["completed_at"], expected_time)
                self.assertIn("**Completed:** " + check.relative_time(expected_time), completed["output"]["summary"])
                self.assertEqual(self.state()["checks"][0]["conclusion"], "success")
                self.assertEqual(len(self.state()["comment_pages"]), 1)
                self.assertEqual(check.status_state(self.state()["status"]), "success")
                for path in (f"repos/{REPO}/issues/7/reactions", f"repos/{REPO}/issues/comments/42/reactions"):
                    owned = [item["content"] for item in self.state()["reactions"][path]
                             if item["user"]["login"] == check.STATUS_AUTHOR]
                    self.assertEqual(owned, ["+1"])

    def test_same_owner_failure_recovery_uses_the_new_published_check_time(self):
        self.run_worker("start")
        self.env["TEST_COMPLETION_TIME"] = "2026-10-07T13:09:19Z"
        self.run_worker("finish")
        self.env.update(REVIEW_RESULT="failure", TEST_COMPLETION_TIME="2026-10-07T14:09:19Z")
        self.run_worker("finish")
        self.assertEqual(check.last_completed_review_details(self.state()["status"])["completed_at"],
                         "2026-10-07T13:09:19Z")
        self.env.update(REVIEW_RESULT="success", TEST_COMPLETION_TIME="2026-10-07T15:09:19Z")
        self.run_worker("finish")
        completed = self.state()["checks"][0]
        self.assertEqual(completed["completed_at"], self.env["TEST_COMPLETION_TIME"])
        self.assertEqual(check.last_completed_review_details(self.state()["status"])["completed_at"],
                         completed["completed_at"])
        self.assertIn("**Completed:** " + check.relative_time(completed["completed_at"]), completed["output"]["summary"])

    def assert_lost_receipt_recovery(self, receipt):
        self.update(findings=[comment(12)])
        self.env["FINDING_PUBLICATION"] = receipt
        self.run_worker("finish", 1)
        state = self.state()
        self.assertEqual(state["checks"][0]["conclusion"], "failure")
        self.assertIn(12, evidence_of(state["checks"][0])["new_finding_comment_ids"])
        self.assertIsNone(state["status"])
        self.env["FINDING_PUBLICATION"] = json.dumps({"attempt_count": 0, "comment_ids": []})
        self.before.write_text("[12]")
        self.update(checks=[*state["checks"], check_run(100, None, status="in_progress")])
        self.env["CHECK_RUN_ID"] = "100"
        self.run_worker("finish")
        self.assertEqual(self.state()["checks"][-1]["conclusion"], "action_required")

    def test_finding_receipt_handoff_and_missing_owner_restart_fail_closed(self):
        # Finding publication itself is outside this fixture: simulate the
        # persisted comment left behind when its cross-job receipt is lost.
        for receipt in ("", json.dumps({"attempt_count": 1, "comment_ids": []})):
            with self.subTest(receipt=receipt):
                self.setUp()
                self.assert_lost_receipt_recovery(receipt)

    def test_restart_at_owner_commit_does_not_bootstrap_missing_authority(self):
        for before in (False, True):
            with self.subTest(before=before):
                self.setUp()
                self.surface("issue_comment")
                self.update(cut=["POST", f"repos/{REPO}/issues/7/comments"], cut_before=before)
                self.run_worker("start", 73)
                committed_status = copy.deepcopy(self.state()["status"])
                self.assertEqual(committed_status is None, before)
                self.run_worker("finish")
                self.assertEqual(self.state()["checks"][0]["conclusion"], "success")
                self.assertEqual(self.state()["status"], committed_status)

    def test_competing_accepted_writers_in_three_finite_schedules(self):
        for boundary in ("running", "check_committed", "completed"):
            for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
                with self.subTest(boundary=boundary, kind=kind):
                    self.setUp()
                    self.surface(kind)
                    self.run_worker("start")
                    old = self.env.copy()
                    if boundary == "check_committed":
                        self.update(cut=["PATCH", f"repos/{REPO}/check-runs/99"])
                        self.run_worker("finish", 73)
                    elif boundary == "completed":
                        self.run_worker("finish")
                    self.env.update(GITHUB_RUN_ID="6", PRESENTATION_START="3")
                    new_check = check_run(100, None, status="in_progress")
                    self.update(checks=[*self.state()["checks"], new_check])
                    self.env["CHECK_RUN_ID"] = "100"
                    self.run_worker("start")
                    if boundary == "completed":
                        self.run_worker("finish")
                    owner = copy.deepcopy(self.state()["status"])
                    current_check = copy.deepcopy(self.state()["checks"][1])
                    calls_before = len(self.state()["calls"])
                    self.env = old
                    self.run_worker("finish")
                    self.assertEqual(self.state()["status"], owner)
                    self.assertEqual(self.state()["checks"][1], current_check)
                    writes = [call for call in self.state()["calls"][calls_before:] if call[0] != "GET"]
                    self.assertLessEqual(len(self.state()["calls"]) - calls_before, 20)
                    self.assertEqual([call[1] for call in writes], [f"repos/{REPO}/check-runs/99"])

    def test_history_outage_then_recovery_retains_sticky_findings(self):
        self.run_worker("start")
        self.update(findings=[comment(12)], outage=True)
        self.run_worker("finish", 1)
        failed = self.state()
        self.assertEqual(failed["checks"][0]["conclusion"], "failure")
        history_calls = [call for call in failed["calls"] if call[0] == "GET" and "/check-runs" in call[1]]
        self.assertEqual(len(history_calls), check.ATTEMPTS)
        self.update(outage=False, checks=[*failed["checks"], check_run(100, None, status="in_progress")])
        self.env["CHECK_RUN_ID"] = "100"
        self.before.write_text("[12]")
        self.update(drift=True)
        prior_calls = len(self.state()["calls"])
        self.run_worker("finish")
        self.assertEqual(self.state()["checks"][-1]["conclusion"], "action_required")
        recovered_reads = [call for call in self.state()["calls"][prior_calls:]
                           if call[0] == "GET" and "/check-runs" in call[1]]
        self.assertEqual(len(recovered_reads), 2)
        before_deadline = self.state()
        self.run_worker("deadline")
        self.assertEqual(self.state(), before_deadline)

    def test_committed_check_restart_after_stale_refresh_publishes_only_historical_clean_signal(self):
        for field in ("head", "base"):
            for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
                with self.subTest(field=field, kind=kind):
                    self.setUp()
                    self.surface(kind)
                    self.env["MANUAL_COMPLETION_ENABLED"] = "true" if kind != "automatic" else "false"
                    self.run_worker("start")
                    captured = self.env.copy()
                    owner = check.status_owner(self.state()["status"])
                    self.update(cut=["PATCH", f"repos/{REPO}/check-runs/99"])
                    self.run_worker("finish", 73)
                    committed = self.state()["checks"][0]
                    self.assertEqual(committed["conclusion"], "success")
                    pr = self.state()["pr"]
                    pr[field]["sha"] = OTHER
                    self.update(pr=pr, base_tip=OTHER if field == "base" else BASE_TIP)
                    if field == "head":
                        self.env["HEAD_SHA"] = OTHER
                    self.run_worker("stale")
                    self.assertEqual(check.status_state(self.state()["status"]), "stale")
                    self.env = captured
                    self.run_worker("finish")
                    state = self.state()
                    evidence = evidence_of(state["checks"][0])
                    self.assertEqual(state["checks"][0]["head_sha"], HEAD)
                    self.assertEqual(state["checks"][0]["conclusion"], "success")
                    self.assertEqual((evidence["reviewed_sha"], evidence["base_sha"],
                                      evidence["merge_base_sha"]), (HEAD, BASE_TIP, MERGE_BASE))
                    self.assertEqual(check.status_owner(state["status"]), owner)
                    self.assertEqual(check.status_state(state["status"]), "stale")
                    self.assertEqual(state["comment_pages"], [])
                    for items in state["reactions"].values():
                        self.assertEqual([item["content"] for item in items if item["user"]["login"] == check.STATUS_AUTHOR
                                          and item["content"] in ("eyes", "+1")], ["+1"])
                    self.assertIn(reaction(701, "heart", login="unrelated-user"),
                                  state["reactions"][f"repos/{REPO}/issues/7/reactions"])

    def test_base_advance_retains_just_completed_review_with_and_without_history(self):
        for previous in (False, True):
            for conclusion in ("success", "action_required"):
                for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
                    with self.subTest(previous=previous, conclusion=conclusion, kind=kind):
                        self.setUp()
                        self.surface(kind)
                        self.env["MANUAL_COMPLETION_ENABLED"] = "true" if kind != "automatic" else "false"
                        old_check = check_run(98, "success")
                        old_check["head_sha"] = OTHER
                        unrelated = {"id": 77, "user": {"login": "human"}, "body": "Keep this comment"}
                        old_finding = comment(11, commit=OTHER)
                        self.update(checks=[old_check, *self.state()["checks"]],
                                    comment_pages=[unrelated], findings=[old_finding])
                        if previous:
                            old_owner = presentation_owner(head=OTHER, base=MERGE_BASE,
                                                           base_ref="release", run=4, started=1)
                            prior_result = "action_required" if conclusion == "success" else "success"
                            with mock.patch.dict(os.environ, BASE_SHA=MERGE_BASE,
                                                 DETAILS_URL="https://github.com/owner/repo/actions/runs/4"):
                                body = check.status_comment_body(OTHER, "release", prior_result, owner=old_owner)
                            self.update(status={"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                                                "body": body})
                        self.run_worker("start")
                        owner = check.status_owner(self.state()["status"])
                        pr = self.state()["pr"]
                        # Cover both an updated PR projection and one still showing the captured base.
                        if previous:
                            pr["base"]["sha"] = OTHER
                        findings = [old_finding, comment(12)] if conclusion == "action_required" else [old_finding]
                        self.update(pr=pr, base_tip=OTHER, findings=findings)
                        self.env["FINDING_PUBLICATION"] = json.dumps({
                            "attempt_count": int(conclusion == "action_required"),
                            "comment_ids": [12] if conclusion == "action_required" else [],
                        })
                        self.run_worker("finish")
                        state = self.state()
                        completed = state["checks"][1]
                        evidence = evidence_of(completed)
                        self.assertEqual(completed["conclusion"], conclusion)
                        self.assertTrue(evidence["completion_verified"])
                        self.assertEqual((evidence["reviewed_sha"], evidence["base_sha"],
                                          evidence["merge_base_sha"]), (HEAD, BASE_TIP, MERGE_BASE))
                        self.assertEqual(check.status_state(state["status"]), "stale")
                        self.assertEqual(check.status_owner(state["status"]), owner)
                        self.assertFalse(check.status_owner_running(state["status"]))
                        self.assertEqual(check.last_completed_review(state["status"]), (HEAD, "main", conclusion))
                        self.assertEqual(check.last_completed_review_details(state["status"]), {
                            "base_sha": BASE_TIP, "run_url": self.env["DETAILS_URL"],
                            "completed_at": completed["completed_at"], "generation": owner["generation"],
                        })
                        body = state["status"]["body"]
                        self.assertIn(check.relative_time(completed["completed_at"]), body)
                        label = "reviewed clean" if conclusion == "success" else "reviewed with findings"
                        self.assertIn(f"**Current commit:** {check.commit_link(HEAD, REPO)} on `main` — {label}", body)
                        self.assertIn(f"**Current baseline:** {check.commit_link(OTHER, REPO)} — integration not reviewed", body)
                        self.assertIn(f"**Reviewed baseline:** {check.commit_link(BASE_TIP, REPO)}", body)
                        self.assertIn(f"[Reviewed workflow run]({self.env['DETAILS_URL']})", body)
                        self.assertIn("## Claude Review", body)
                        self.assertIn(check.STATUS_REASONS["base_advanced"], body)
                        self.assertNotIn("runs/4)", body)
                        self.assertEqual(state["checks"][0], old_check)
                        self.assertEqual(state["findings"], findings)
                        self.assertEqual(state["comment_pages"], [unrelated])  # No clean completion notice.
                        for items in state["reactions"].values():
                            self.assertEqual([item["content"] for item in items if item["user"]["login"] == check.STATUS_AUTHOR
                                              and item["content"] in ("eyes", "+1")],
                                             ["+1"] if conclusion == "success" else [])
                        self.assertIn(reaction(701, "heart", login="unrelated-user"),
                                      state["reactions"][f"repos/{REPO}/issues/7/reactions"])
                        # A fresh process and a later status run cannot relabel the reviewed base/run.
                        self.env["DETAILS_URL"] = "https://github.com/owner/repo/actions/runs/6"
                        self.update(base_tip=MERGE_BASE)
                        self.run_worker("stale")
                        refreshed = self.state()["status"]
                        self.assertEqual(check.last_completed_review(refreshed), (HEAD, "main", conclusion))
                        self.assertEqual(check.last_completed_review_details(refreshed), {
                            "base_sha": BASE_TIP, "run_url": "https://github.com/owner/repo/actions/runs/5",
                            "completed_at": completed["completed_at"], "generation": owner["generation"],
                        })
                        self.assertIn(check.relative_time(completed["completed_at"]), refreshed["body"])
                        self.assertIn(f"**Current baseline:** {check.commit_link(MERGE_BASE, REPO)} — integration not reviewed", refreshed["body"])
                        self.assertIn("## Claude Review", refreshed["body"])
                        self.assertNotIn("runs/6)", refreshed["body"])

    def test_deleted_finding_after_outage_survives_failed_attempt_and_restart(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            with self.subTest(kind=kind):
                self.setUp()
                self.surface(kind)
                self.env["MANUAL_COMPLETION_ENABLED"] = "true" if kind != "automatic" else "false"
                self.run_worker("start")
                self.update(findings=[comment(12)], outage=True)
                self.env["FINDING_PUBLICATION"] = ""  # Lost receipt accompanies unavailable history.
                self.run_worker("finish", 1)
                failed = copy.deepcopy(self.state()["checks"][0])
                self.assertEqual(failed["conclusion"], "failure")
                self.assertTrue(evidence_of(failed)["same_diff_findings_recorded"])
                self.assertEqual(evidence_of(failed)["new_finding_comment_ids"], [12])
                self.assertEqual(len([call for call in self.state()["calls"]
                                     if call[0] == "GET" and "/check-runs" in call[1]]), check.ATTEMPTS)
                # The next attempt sees no surviving comment. Only the failed
                # Check can retain this finding's captured-diff attribution.
                self.update(findings=[], outage=False, drift=True,
                            checks=[failed, check_run(100, None, status="in_progress")])
                self.env.update(CHECK_RUN_ID="100", GITHUB_RUN_ID="6", PRESENTATION_START="3",
                                FINDING_PUBLICATION=json.dumps({"attempt_count": 0, "comment_ids": []}))
                self.run_worker("start")
                prior_calls = len(self.state()["calls"])
                self.run_worker("finish")
                state = self.state()
                self.assertEqual(state["checks"][0], failed)
                self.assertEqual(state["checks"][1]["conclusion"], "action_required")
                evidence = evidence_of(state["checks"][1])
                self.assertEqual(evidence["prior_finding_check_run_ids"], [99])
                self.assertEqual(evidence["evidence_errors"], [])
                self.assertEqual(state["comment_pages"], [])
                reads = [call for call in state["calls"][prior_calls:]
                         if call[0] == "GET" and "/check-runs" in call[1]]
                self.assertEqual(len(reads), 2)
                self.assertEqual(check.status_state(state["status"]), "action_required")
                for items in state["reactions"].values():
                    self.assertFalse([item for item in items if item["user"]["login"] == check.STATUS_AUTHOR
                                      and item["content"] in ("eyes", "+1")])

    def test_reaction_commit_restart_with_or_without_superseding_attempt(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            opening = f"repos/{REPO}/issues/7/reactions"
            paths = [opening]
            if kind != "automatic":
                namespace = "issues" if kind == "issue_comment" else "pulls"
                paths.append(f"repos/{REPO}/{namespace}/comments/42/reactions")
            for path in paths:
                for before in (False, True):
                    for newer in (False, True):
                        with self.subTest(kind=kind, path=path, before=before, newer=newer):
                            self.setUp()
                            self.surface(kind)
                            preserved = [reaction(702, "+1", login="unrelated-user"),
                                         reaction(703, "eyes", login="other-bot")]
                            reactions = self.state()["reactions"]
                            for surface in paths:
                                reactions.setdefault(surface, []).extend(preserved)
                            self.update(reactions=reactions)
                            self.run_worker("start")
                            old = self.env.copy()
                            self.update(cut=["POST", path], cut_before=before)
                            self.run_worker("finish", 73)
                            cut_state = self.state()
                            owned = [item["content"] for item in cut_state["reactions"][path]
                                     if item["user"]["login"] == check.STATUS_AUTHOR]
                            self.assertEqual(owned, [] if before else ["+1"])
                            if newer:
                                self.env.update(GITHUB_RUN_ID="6", GITHUB_RUN_ATTEMPT="2",
                                                PRESENTATION_START="3", CHECK_RUN_ID="100")
                                self.update(checks=[*cut_state["checks"],
                                                    check_run(100, None, status="in_progress")])
                                self.run_worker("start")
                            durable = copy.deepcopy(self.state())
                            prior_calls = len(durable["calls"])
                            self.env = old
                            self.run_worker("finish")
                            state = self.state()
                            self.assertEqual(state["checks"][0]["conclusion"], "success")
                            if newer:
                                self.assertEqual(state["status"], durable["status"])
                                self.assertEqual(state["checks"][1], durable["checks"][1])
                                self.assertEqual(state["reactions"], durable["reactions"])
                                writes = [call for call in state["calls"][prior_calls:] if call[0] != "GET"]
                                self.assertEqual([call[1] for call in writes], [f"repos/{REPO}/check-runs/99"])
                            else:
                                self.assertEqual(check.status_state(state["status"]), "success")
                                self.assertEqual(check.status_owner(state["status"]),
                                                 check.status_owner(durable["status"]))
                            for surface in paths:
                                items = state["reactions"][surface]
                                self.assertTrue(all(item in items for item in preserved))
                                owned = [item["content"] for item in items
                                         if item["user"]["login"] == check.STATUS_AUTHOR]
                                self.assertEqual(owned, ["eyes"] if newer else ["+1"])
                            self.assertLessEqual(len(state["calls"]) - prior_calls, 100)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        sys.exit(worker(sys.argv[2], sys.argv[3]))
    unittest.main()
