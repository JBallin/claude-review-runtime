"""Deterministic target-push discovery and queued publication; no provider calls."""
import copy
import json
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs

from test_claude_review_check import check, REPO, HEAD, OTHER, NEWER, BASE_TIP
import test_presentation_ownership as ownership
import test_claude_review_check as fixtures
import test_claude_review_workflows as w


def pull(number=7, base="main", state="open", repo=REPO):
    return {"number": number, "state": state, "base": {"ref": base, "repo": {"full_name": repo}}}


class DiscoveryTests(unittest.TestCase):
    def discover(self, pages, **env):
        output, calls = {}, []
        def api(args, body=None):
            self.assertIsNone(body)
            self.assertNotIn("--method", args)
            calls.append(args[0])
            query = parse_qs(args[0].split("?", 1)[1])
            self.assertEqual(query["state"], ["open"])
            self.assertEqual(query["base"], [env.get("PUSH_REF", "main")])
            return pages[int(query["page"][0]) - 1]
        with mock.patch.dict(os.environ, {"REPO": REPO, "PUSH_REF": "main", "PUSH_SHA": OTHER,
                                          "CLAUDE_REVIEW_RETRY_DELAY": "0", **env}, clear=True), \
             mock.patch.object(check, "direct_base_tip", return_value=OTHER), \
             mock.patch.object(check, "gh_api", side_effect=api), \
             mock.patch.object(check, "write_output", side_effect=lambda k, v: output.update({k: v})):
            result = check.cmd_discover_target()
        self.assertIsNone(check.PRESENTATION_DEADLINE)
        return result, output, calls

    def test_pages_exact_filter_and_deduplication(self):
        first = [pull(i) for i in range(1, 101)]
        second = [pull(100), pull(101), pull(102, base="main-extra"), pull(103, state="closed"),
                  pull(104, repo="other/repo")]
        result, output, calls = self.discover([json.dumps(first), json.dumps(second)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output["prs"]), list(range(1, 102)))
        self.assertEqual(len(calls), 2)

    def test_slash_and_query_characters_are_encoded_as_data(self):
        ref = "release/2026&extra=value"
        result, output, _ = self.discover([json.dumps([pull(base=ref)])], PUSH_REF=ref)
        self.assertEqual((result, output), (0, {"prs": "[7]"}))

    def test_empty_and_deleted_or_invalid_push(self):
        self.assertEqual(self.discover(["[]"])[:2], (0, {"prs": "[]"}))
        for value in ("0" * 40, "bad", OTHER.upper()):
            result, output, calls = self.discover([], PUSH_SHA=value)
            self.assertEqual((result, output, calls), (1, {}, []))

    def test_fanout_overflow_or_malformed_page_emits_no_partial_output(self):
        for pages in ([json.dumps([pull(i) for i in range(1, 101)]),
                       json.dumps([pull(i) for i in range(101, 201)]),
                       json.dumps([pull(i) for i in range(201, 258)])],
                      ["{}"], ["{"], [json.dumps([pull(number=True)])]):
            result, output, _ = self.discover(pages)
            self.assertEqual((result, output), (1, {}))

    def test_exact_matrix_limit_is_supported(self):
        pages = [json.dumps([pull(i) for i in range(start, min(start + 100, 257))])
                 for start in (1, 101, 201)]
        result, output, calls = self.discover(pages)
        self.assertEqual(result, 0)
        self.assertEqual(len(json.loads(output["prs"])), 256)
        self.assertEqual(len(calls), 3)

    def test_page_bound_is_not_silent_truncation(self):
        with mock.patch.object(check, "TARGET_PAGE_LIMIT", 1):
            result, output, calls = self.discover([json.dumps([pull(i) for i in range(1, 101)])])
        self.assertEqual((result, output, len(calls)), (1, {}, 1))

    def test_discovery_outage_and_superseded_push(self):
        for tips, expected in (([NEWER], (0, {"prs": "[]"})),
                               ([OTHER, NEWER], (0, {"prs": "[]"})),
                               ([OTHER], (1, {}))):
            output = {}
            with mock.patch.dict(os.environ, {"REPO": REPO, "PUSH_REF": "main", "PUSH_SHA": OTHER,
                                               "CLAUDE_REVIEW_RETRY_DELAY": "0"}, clear=True), \
                 mock.patch.object(check, "direct_base_tip", side_effect=tips), \
                 mock.patch.object(check, "gh_api", side_effect=check.GhError("outage") if len(tips) == 1 else None,
                                   return_value="[]"), \
                 mock.patch.object(check, "write_output", side_effect=lambda k, v: output.update({k: v})):
                self.assertEqual((check.cmd_discover_target(), output), expected)


class RefreshTests(unittest.TestCase):
    setUp = ownership.OwnershipTests.setUp
    start = ownership.OwnershipTests.start

    def prepare(self, state="success"):
        self.api.pr["state"] = "open"
        os.environ["DETAILS_URL"] = "https://github.com/owner/repo/actions/runs/5"
        self.start()
        check.best_effort_status(HEAD, state, completed_at="2026-10-07T13:09:19Z")
        self.before = copy.deepcopy(self.api.status)
        self.reactions = copy.deepcopy(self.api.reactions)
        self.api.base_tip = OTHER
        os.environ.update(PUSH_REF="main", PUSH_SHA=OTHER, PR_REACTION_MODE="stale")
        self.api.calls.clear()

    def writes(self):
        return [c for c in self.api.calls if c[0] != "GET"]

    def test_push_refresh_preserves_completed_evidence_and_historical_reactions(self):
        for result in ("success", "action_required"):
            with self.subTest(result=result):
                os.environ.pop("PUSH_REF", None)
                self.api.base_tip = BASE_TIP
                self.prepare(result)
                details = check.last_completed_review_details(self.before)
                self.assertEqual(check.cmd_refresh_target(), 0)
                self.assertEqual(check.status_state(self.api.status), "stale")
                self.assertEqual(check.status_owner(self.api.status), check.status_owner(self.before))
                self.assertEqual(check.last_completed_review_details(self.api.status), details)
                self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", result))
                self.assertIn(check.TARGET_TIP_WARNING, self.api.status["body"])
                self.assertEqual(self.api.reactions, self.reactions)
                self.assertTrue(self.writes())
                self.assertTrue(all(c[0] == "PATCH" and c[1].endswith("issues/comments/55") for c in self.writes()))

    def test_closed_retargeted_superseded_and_missing_status_do_not_write(self):
        self.prepare()
        for changed in ("closed", "retarget", "new-tip", "no-status", "untrusted"):
            with self.subTest(changed=changed):
                self.api.status = copy.deepcopy(self.before)
                self.api.base_tip = OTHER
                self.api.pr.update(state="open")
                self.api.pr["base"]["ref"] = "main"
                if changed == "closed": self.api.pr["state"] = "closed"
                if changed == "retarget": self.api.pr["base"]["ref"] = "release"
                if changed == "new-tip": self.api.base_tip = NEWER
                if changed == "no-status": self.api.status = None
                if changed == "untrusted": self.api.status["user"]["login"] = "someone"
                self.api.calls.clear()
                self.assertEqual(check.cmd_refresh_target(), 0)
                self.assertEqual(self.writes(), [])

    def test_movement_or_new_owner_just_before_publication_suppresses_write(self):
        self.prepare()
        for changed in ("closed", "retarget", "tip", "head", "owner"):
            with self.subTest(changed=changed):
                self.api.status = copy.deepcopy(self.before)
                self.api.base_tip = OTHER
                self.api.pr["state"] = "open"
                self.api.pr["head"]["sha"] = HEAD
                self.api.pr["base"]["ref"] = "main"
                count = 0
                def race(method, path):
                    nonlocal count
                    if path.endswith("issues/7/comments"):
                        count += 1
                        if count == 2:
                            if changed == "closed": self.api.pr["state"] = "closed"
                            if changed == "retarget": self.api.pr["base"]["ref"] = "release"
                            if changed == "tip": self.api.base_tip = NEWER
                            if changed == "head": self.api.pr["head"]["sha"] = NEWER
                            if changed == "owner": self.api.status["body"] += "\nnewer publication"
                self.api.on_call = race
                self.api.calls.clear()
                self.assertEqual(check.cmd_refresh_target(), 0)
                self.assertEqual(self.writes(), [])

    def test_lookup_or_publication_failure_never_changes_checks(self):
        self.prepare()
        self.api.faults[("PATCH", f"repos/{REPO}/issues/comments/55")] = "fail"
        self.assertEqual(check.cmd_refresh_target(), 0)
        self.assertEqual(self.api.status, self.before)
        self.assertFalse(any("check-runs" in c[1] or "/pulls/7/comments" in c[1] for c in self.api.calls))


class CommandTests(fixtures.ScriptTestCase):
    def test_actual_discovery_command_emits_only_numeric_matrix(self):
        path = f"repos/{REPO}/pulls?state=open&base=main&per_page=100&page=1"
        result = self.run_script(["discover-target"], [{"method": "GET", "path": path,
            "responses": [fixtures.ok(json.dumps([pull()]))]}], PUSH_REF="main", PUSH_SHA=BASE_TIP)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("prs=[7]", self.output.read_text())
        self.assertTrue(all(call["method"] == "GET" for call in self.calls()))

    def test_actual_refresh_command_skips_closed_pr_without_writes(self):
        response = json.loads(fixtures.live_head(HEAD)["stdout"])
        response["state"] = "closed"
        result = self.run_script(["refresh-target"], [fixtures.pr_head_rule(fixtures.ok(json.dumps(response)))],
                                 PUSH_REF="main", PUSH_SHA=BASE_TIP, STATUS_COMMENTS_ENABLED="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("closed or no longer targets", result.stdout)
        self.assertTrue(all(call["method"] == "GET" for call in self.calls()))


class WorkflowTests(unittest.TestCase):
    def test_push_jobs_are_read_only_discovery_then_bounded_per_pr_queue(self):
        discover = w.job(w.STALE, "discover-target")
        refresh = w.job(w.STALE, "refresh-target")
        self.assertIn("github.event_name == 'push'", w.scalar(discover, "if", 4))
        self.assertIn("github.event.deleted == false", w.scalar(discover, "if", 4))
        self.assertEqual(w.scalar(discover, "timeout-minutes", 4), "3")
        self.assertEqual(w.block(discover, "permissions", 4), ["      contents: read", "      pull-requests: read"])
        self.assertEqual(w.scalar(refresh, "needs", 4), "discover-target")
        strategy = w.block(refresh, "strategy", 4)
        self.assertEqual(w.scalar(strategy, "max-parallel", 6), "4")
        self.assertEqual(w.scalar(strategy, "fail-fast", 6), "false")
        queue = w.block(refresh, "concurrency", 4)
        self.assertEqual(w.scalar(queue, "group", 6),
                         "${{ format('claude-review-state-{0}-{1}', github.repository, matrix.pr) }}")
        self.assertEqual(w.scalar(queue, "queue", 6), "max")
        self.assertEqual(w.scalar(queue, "cancel-in-progress", 6), "false")
        text = w.STALE.read_text()
        for forbidden in ("checks: write", "id-token: write", "actions/checkout", "secrets:", "anthropics/"):
            self.assertNotIn(forbidden, text)
        self.assertIn('python3 "$REVIEW_HELPER" refresh-target', text)
