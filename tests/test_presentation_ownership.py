"""Stateful offline tests for trusted request ownership and both reaction surfaces."""
import copy
import json
import os
import unittest
from unittest import mock

from test_claude_review_check import check, HEAD, OTHER, BASE_TIP, REPO, reaction, presentation_owner


class PresentationAPI:
    def __init__(self):
        self.status = None
        self.pr = {"head": {"sha": HEAD}, "base": {"sha": BASE_TIP, "ref": "main"}}
        self.reactions = {}
        self.targets = {}
        self.calls = []
        self.faults = {}
        self.comment_pages = []
        self.on_call = None

    def call(self, args, body=None):
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        path = next(item for item in args if item not in ("--method", "--paginate", "POST", "PATCH", "DELETE", "--input", "-"))
        path = path.split("?")[0]
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.on_call:
            self.on_call(method, path)
        fault = self.faults.get((method, path))
        if fault == "fail":
            raise check.GhError("injected failure")
        if path == f"repos/{REPO}/pulls/7":
            result = json.dumps(self.pr)
        elif path == f"repos/{REPO}/issues/7/comments" and method == "GET":
            result = "".join(json.dumps(page) for page in [self.comment_pages, [self.status] if self.status else []])
        elif (path == f"repos/{REPO}/issues/7/comments" and method == "POST"
              or path == f"repos/{REPO}/issues/comments/55" and method == "PATCH"):
            self.status = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"}, "body": body["body"]}
            result = json.dumps(self.status)
        elif path.endswith("/reactions"):
            items = self.reactions.setdefault(path, [])
            if method == "GET":
                # Two pages are always served; owned entries may live on either.
                result = json.dumps(items[:1]) + json.dumps(items[1:])
            elif method == "POST":
                items.append(reaction(100 + len(items), body["content"]))
                result = json.dumps(items[-1])
            else:
                raise AssertionError((method, path))
        elif "/reactions/" in path and method == "DELETE":
            parent, item_id = path.rsplit("/", 1)
            self.reactions[parent] = [item for item in self.reactions[parent] if item["id"] != int(item_id)]
            result = ""
        elif path in self.targets:
            result = json.dumps(self.targets[path])
        else:
            raise check.GhError("HTTP 404: unknown target")
        if fault == "lost":
            raise check.GhError("response lost after write")
        return result


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.api = PresentationAPI()
        self.output = []
        self.env = {"STATUS_COMMENTS_ENABLED": "true", "REPO": REPO, "PR_NUMBER": "7",
                    "HEAD_SHA": HEAD, "BASE_SHA": BASE_TIP, "BASE_REF": "main",
                    "GITHUB_RUN_ID": "5", "GITHUB_RUN_ATTEMPT": "1", "PRESENTATION_START": "2",
                    "TRIGGER_KIND": "automatic", "PR_REACTION_MODE": "automatic",
                    "CLAUDE_REVIEW_RETRY_DELAY": "0"}
        self.stack = mock.patch.dict(os.environ, self.env, clear=True)
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.real_gh = check.gh_api
        self.gh = mock.patch.object(check, "gh_api", side_effect=self.api.call)
        self.gh_mock = self.gh.start()
        self.addCleanup(self.gh.stop)
        self.outputs = mock.patch.object(check, "write_output", side_effect=lambda name, value: self.output.append((name, value)))
        self.outputs.start()
        self.addCleanup(self.outputs.stop)
        self.opening = f"repos/{REPO}/issues/7/reactions"

    def target(self, kind, target=42):
        namespace = "issues" if kind == "issue_comment" else "pulls"
        path = f"repos/{REPO}/{namespace}/comments/{target}"
        self.api.targets[path] = {"id": target, "issue_url" if namespace == "issues" else "pull_request_url":
                                  f"https://api.github.com/repos/{REPO}/{namespace}/7"}
        os.environ.update(TRIGGER_KIND=kind, TRIGGER_COMMENT_ID=str(target), PR_REACTION_MODE="manual")
        return path + "/reactions"

    def start(self, **changes):
        os.environ.update({key: str(value) for key, value in changes.items()})
        check.best_effort_status(HEAD, "in_progress", acquire=True)
        for name, value in self.output:
            if name == "presentation_owner":
                os.environ["PRESENTATION_OWNER"] = value
        return check.status_owner(self.api.status)

    def finish(self, state="success", available=True):
        check.best_effort_status(HEAD, state, check_available=available)

    def owned(self, path):
        return [item["content"] for item in self.api.reactions.get(path, [])
                if item["user"]["login"] == check.STATUS_AUTHOR and item["content"] in ("eyes", "+1")]

    def writes(self):
        return [call for call in self.api.calls if call[0] != "GET"]

    def test_base_tip_advancement_finishes_owned_presentation_as_stale(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            with self.subTest(kind=kind):
                self.api = PresentationAPI()
                self.gh_mock.side_effect = self.api.call
                os.environ.update(TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic")
                trigger = self.target(kind) if kind != "automatic" else None
                owner = self.start()
                self.api.pr["base"]["sha"] = OTHER
                self.finish()
                self.assertEqual(check.status_state(self.api.status), "stale")
                self.assertEqual(check.status_owner(self.api.status), owner)
                self.assertFalse(check.status_owner_running(self.api.status))
                self.assertEqual(self.owned(self.opening), [])
                if trigger:
                    self.assertEqual(self.owned(trigger), [])

    def test_base_tip_movement_does_not_reclaim_or_change_terminal_history(self):
        path = self.target("issue_comment")
        self.start()
        self.finish()
        previous = copy.deepcopy(self.api.status)
        self.api.pr["base"]["sha"] = OTHER
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.api.status, previous)
        self.assertEqual(self.owned(path), ["+1"])
        self.start(GITHUB_RUN_ID="6", PRESENTATION_START="3")
        self.assertEqual(self.api.status, previous)
        self.assertEqual(self.writes(), [])

    def test_reaction_summary_changes_do_not_block_start_or_finish(self):
        trigger = self.target("issue_comment")
        self.start()
        def change_summary(method, path):
            if method == "GET" and path.endswith("/issues/7/comments"):
                self.api.status["reactions"] = {"total_count": len(self.api.calls)}
        self.api.on_call = change_summary
        self.finish()
        self.assertEqual(check.status_state(self.api.status), "success")
        self.assertEqual(self.owned(self.opening), ["+1"])
        self.assertEqual(self.owned(trigger), ["+1"])
        self.start(GITHUB_RUN_ID="6", PRESENTATION_START="3")
        self.assertEqual(check.status_state(self.api.status), "in_progress")
        self.assertEqual(check.status_owner(self.api.status)["run"], 6)

    def test_status_body_change_between_reads_blocks_publication(self):
        self.start()
        reads = 0
        def change_body(method, path):
            nonlocal reads
            if method == "GET" and path.endswith("/issues/7/comments"):
                reads += 1
                if reads == 2:
                    self.api.status["body"] += "\nConcurrent body edit"
        self.api.on_call = change_body
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.owned(self.opening), ["eyes"])

    def test_complete_manual_and_automatic_matrix(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for state, available, expected in (("success", True, ["+1"]), ("action_required", True, []),
                                              ("failure", True, []), ("publication_incomplete", True, []),
                                              ("success", False, []), ("publication_incomplete", False, [])):
                with self.subTest(kind=kind, state=state, available=available):
                    self.api.status = None
                    self.api.reactions.clear()
                    self.output.clear()
                    os.environ.update(TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic")
                    os.environ.pop("TRIGGER_COMMENT_ID", None)
                    path = self.target(kind) if kind != "automatic" else None
                    owner = self.start()
                    self.assertIsNotNone(owner)
                    self.assertEqual(self.owned(self.opening), ["eyes"])
                    if path:
                        self.assertEqual(self.owned(path), ["eyes"])
                    self.finish(state, available)
                    self.assertEqual(self.owned(self.opening), expected)
                    if path:
                        self.assertEqual(self.owned(path), expected)
                    else:
                        self.assertFalse(any("/comments/42" in call[1] for call in self.api.calls))
                    self.api.calls.clear()

    def test_unavailable_check_running_eyes_then_terminal_cleanup(self):
        path = self.target("issue_comment")
        check.best_effort_status(HEAD, "in_progress", check_available=False, acquire=True)
        os.environ["PRESENTATION_OWNER"] = next(value for name, value in self.output if name == "presentation_owner")
        self.assertEqual(self.owned(path), ["eyes"])
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.finish("publication_incomplete", False)
        self.assertEqual(self.owned(path) + self.owned(self.opening), [])

    def test_duplicate_start_does_not_regress_terminal(self):
        owner = self.start()
        self.finish()
        self.api.calls.clear()
        self.start()
        self.assertEqual(check.status_owner(self.api.status), owner)
        self.assertEqual(self.owned(self.opening), ["+1"])
        self.assertEqual(self.writes(), [])

    def test_duplicate_active_start_is_idempotent(self):
        self.start()
        self.api.calls.clear()
        self.start()
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertFalse(any(call[0] in ("POST", "DELETE") for call in self.api.calls))

    def test_a_b_then_old_partial_rerun_cannot_claim_b(self):
        a = self.target("issue_comment", 42)
        owner_a = self.start()
        self.finish()
        b = self.target("issue_comment", 43)
        owner_b = self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3)
        self.finish()
        self.api.calls.clear()
        os.environ.update(PRESENTATION_OWNER=json.dumps(owner_a), GITHUB_RUN_ID="5", GITHUB_RUN_ATTEMPT="2")
        self.finish("failure")
        self.assertEqual(self.writes(), [])
        self.assertEqual(check.status_owner(self.api.status), owner_b)
        self.assertEqual(self.owned(a) + self.owned(b), ["+1", "+1"])

    def test_a_b_then_new_a_start_can_acquire_same_head(self):
        a = self.target("issue_comment", 42)
        self.start()
        self.finish()
        b = self.target("issue_comment", 43)
        self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3)
        self.finish()
        self.target("issue_comment", 42)
        new_a = self.start(GITHUB_RUN_ID=5, GITHUB_RUN_ATTEMPT=2, PRESENTATION_START=4)
        self.assertEqual((new_a["run"], new_a["attempt"]), (5, 2))
        self.assertEqual(self.owned(a), ["eyes"])
        self.assertEqual(self.owned(b), ["+1"])
        self.assertEqual(self.owned(self.opening), ["eyes"])

    def test_duplicate_old_start_cannot_reclaim_newer_owner(self):
        self.target("issue_comment", 42)
        self.start()
        self.target("issue_comment", 43)
        owner_b = self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3)
        self.api.calls.clear()
        self.target("issue_comment", 42)
        self.start(GITHUB_RUN_ID=5, PRESENTATION_START=2)
        self.assertEqual(self.writes(), [])
        self.assertEqual(check.status_owner(self.api.status), owner_b)

    def test_failed_jobs_rerun_requires_new_start(self):
        self.start()
        self.api.calls.clear()
        os.environ["GITHUB_RUN_ATTEMPT"] = "2"
        self.finish()
        self.assertEqual(self.writes(), [])
        owner = self.start(PRESENTATION_START=3)
        self.finish()
        self.assertEqual(owner["attempt"], 2)
        self.assertEqual(self.owned(self.opening), ["+1"])

    def test_old_same_comment_attempt_cannot_clear_new_attempt_eyes(self):
        path = self.target("issue_comment")
        old = self.start()
        newer = self.start(GITHUB_RUN_ATTEMPT=2, PRESENTATION_START=3)
        self.api.calls.clear()
        os.environ.update(PRESENTATION_OWNER=json.dumps(old), GITHUB_RUN_ATTEMPT="1")
        self.finish("failure")
        self.assertEqual(self.writes(), [])
        self.assertEqual(check.status_owner(self.api.status), newer)
        self.assertEqual(self.owned(path) + self.owned(self.opening), ["eyes", "eyes"])

    def test_missing_owner_blocks_finalizer_but_new_start_bootstraps(self):
        os.environ["PRESENTATION_OWNER"] = json.dumps(presentation_owner())
        self.finish()
        self.assertEqual(self.writes(), [])
        self.assertIsNotNone(self.start())

    def test_legacy_owner_blocks_finalizer_but_start_preserves_review_history(self):
        self.api.status = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                           "body": check.status_comment_body(HEAD, "main", "success")}
        os.environ["PRESENTATION_OWNER"] = json.dumps(presentation_owner())
        self.finish("failure")
        self.assertEqual(self.writes(), [])
        self.start()
        self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", "success"))

    def test_failed_owner_publication_suppresses_reactions_and_outputs(self):
        self.api.faults[("POST", f"repos/{REPO}/issues/7/comments")] = "fail"
        self.start()
        self.assertEqual(self.output, [])
        self.assertEqual(self.owned(self.opening), [])
        self.assertEqual(len([c for c in self.writes() if c[0] == "POST"]), 1)

    def test_lost_owner_publication_response_is_confirmed_without_duplicate_post(self):
        self.api.faults[("POST", f"repos/{REPO}/issues/7/comments")] = "lost"
        self.assertIsNotNone(self.start())
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertEqual(len([c for c in self.writes() if c[1].endswith("/comments")]), 1)

    def test_invalid_target_does_not_redirect_or_block_opening(self):
        path = self.target("pull_request_review_comment")
        self.api.targets[path.removesuffix("/reactions")]["pull_request_url"] = "https://api.github.com/repos/other/repo/pulls/7"
        self.start()
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertEqual(self.owned(path), [])
        self.assertFalse(any(call[1] == path for call in self.api.calls))

    def test_deleted_trigger_is_best_effort(self):
        path = self.target("issue_comment")
        del self.api.targets[path.removesuffix("/reactions")]
        self.start()
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertEqual(self.owned(path), [])

    def test_pagination_dedup_and_preservation_on_both_surfaces(self):
        path = self.target("issue_comment")
        for surface in (self.opening, path):
            self.api.reactions[surface] = [reaction(1, "eyes"), reaction(2, "eyes"), reaction(3, "+1"),
                                           reaction(4, "eyes", login="claude[bot]"),
                                           reaction(5, "+1", login="human"), reaction(6, "heart")]
        self.start()
        for surface in (self.opening, path):
            self.assertEqual(self.owned(surface), ["eyes"])
            self.assertEqual([item["id"] for item in self.api.reactions[surface]], [1, 4, 5, 6])

    def test_delete_failure_suppresses_mixed_reactions_only_on_affected_surface(self):
        path = self.target("issue_comment")
        self.start()
        self.api.faults[("DELETE", self.opening + "/100")] = "fail"
        self.finish()
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertEqual(self.owned(path), ["+1"])

    def test_lost_delete_response_confirmed_before_add(self):
        self.start()
        self.api.faults[("DELETE", self.opening + "/100")] = "lost"
        self.finish()
        self.assertEqual(self.owned(self.opening), ["+1"])

    def test_ambiguous_post_readback_never_blindly_reposts(self):
        self.api.faults[("POST", self.opening)] = "lost"
        self.start()
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertEqual(len([c for c in self.writes() if c[1] == self.opening]), 1)

    def test_missing_post_after_failure_remains_best_effort(self):
        self.api.faults[("POST", self.opening)] = "fail"
        self.start()
        self.assertEqual(self.owned(self.opening), [])
        self.assertIsNotNone(check.status_owner(self.api.status))

    def test_status_write_failure_cleans_reactions_without_claiming_success(self):
        path = self.target("issue_comment")
        self.start()
        self.api.faults[("PATCH", f"repos/{REPO}/issues/comments/55")] = "fail"
        self.finish()
        self.assertEqual(self.owned(self.opening) + self.owned(path), [])

    def test_stale_retains_owner_and_terminal_trigger_history(self):
        path = self.target("issue_comment")
        owner = self.start()
        self.finish()
        self.api.pr["head"]["sha"] = OTHER
        os.environ["HEAD_SHA"] = OTHER
        check.best_effort_status(OTHER, "stale")
        self.assertEqual(check.status_owner(self.api.status), owner)
        self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", "success"))
        self.assertEqual(self.owned(self.opening), [])
        self.assertEqual(self.owned(path), ["+1"])

    def test_stale_running_owner_can_finish_with_trigger_cleanup(self):
        path = self.target("issue_comment")
        owner = self.start()
        self.api.pr["head"]["sha"] = OTHER
        os.environ["HEAD_SHA"] = OTHER
        check.best_effort_status(OTHER, "stale")
        self.assertTrue(check.status_owner_running(self.api.status))
        os.environ["HEAD_SHA"] = HEAD
        self.finish("failure")
        self.assertEqual(self.owned(path), [])
        self.assertEqual(check.status_owner(self.api.status), owner)

    def test_delayed_stale_event_cannot_clear_current_review(self):
        self.start()
        self.api.calls.clear()
        check.best_effort_status(HEAD, "stale")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.owned(self.opening), ["eyes"])

    def test_superseded_active_attempt_only_cleans_its_trigger(self):
        path = self.target("issue_comment")
        self.start()
        self.api.pr["head"]["sha"] = OTHER
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.owned(path), [])
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertFalse(any(call[0] != "GET" and call[1] == self.opening for call in self.api.calls))

    def test_superseded_terminal_attempt_preserves_historical_thumbs(self):
        path = self.target("issue_comment")
        self.start()
        self.finish()
        self.api.pr["head"]["sha"] = OTHER
        self.finish()
        self.assertEqual(self.owned(path), ["+1"])

    def test_owner_change_during_reconciliation_stops_old_writes(self):
        self.start()
        changed = False
        def switch(method, path):
            nonlocal changed
            if path == self.opening and method == "GET" and not changed:
                changed = True
                self.api.status["body"] = check.status_comment_body(HEAD, "main", "in_progress", owner=presentation_owner(run=6, started=3))
        self.api.on_call = switch
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.owned(self.opening), ["eyes"])
        self.assertFalse(any(call[0] in ("POST", "DELETE") for call in self.api.calls))

    def test_invalid_owner_schema_or_digest_suppresses_writes(self):
        self.start()
        for change in ({"generation": "0" * 64}, {"target": True}, {"run": "5"}, {"kind": "arbitrary"}):
            with self.subTest(change=change):
                os.environ["PRESENTATION_OWNER"] = json.dumps({**presentation_owner(), **change})
                self.api.calls.clear()
                self.finish()
                self.assertEqual(self.writes(), [])

    def test_foreign_stale_owner_cannot_authorize_writes(self):
        self.start()
        self.api.status["body"] = check.status_comment_body(HEAD, "main", "success", owner=presentation_owner(repo="other/repo"))
        self.api.calls.clear()
        check.best_effort_status(OTHER, "stale")
        self.assertEqual(self.writes(), [])

    def test_presentation_budget_exhaustion_resets_deadline(self):
        times = iter([0, 121])
        with mock.patch.object(check.time, "monotonic", side_effect=lambda: next(times, 121)):
            # Exercise actual gh_api budget before subprocess execution.
            with mock.patch.object(check, "gh_api", wraps=self.real_gh):
                self.start()
        self.assertIsNone(check.PRESENTATION_DEADLINE)

class TrustedOutcomeIntegrationTests(unittest.TestCase):
    """The real finalizer, including #7 verification, feeds presentation state."""
    def test_verified_outcomes_drive_both_manual_surfaces(self):
        from test_claude_review_check import (ScriptTestCase, history_rule, current_run_only,
            comments_rule, comment, patch_rule, status_list_rule, status_patch_rule, pages, ok,
            reaction_list_rule, reaction_delete_rule, reaction_post_rule)
        cases = [({}, [], "success", "+1"),
                 ({}, [comment(9)], "action_required", None),
                 ({"COMPLETION_VERIFIED": "false"}, [], "failure", None),
                 ({"REVIEW_RESULT": "cancelled"}, [], "failure", None),
                 ({"FINDING_PUBLICATION": '{"attempt_count":1,"comment_ids":[9]}'}, [], "failure", None)]
        for kind in ("issue_comment", "pull_request_review_comment"):
            for changes, findings, conclusion, desired in cases:
                with self.subTest(kind=kind, changes=changes):
                    harness = ScriptTestCase()
                    harness.setUp()
                    try:
                        owner = presentation_owner(kind=kind, target=42)
                        status = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                                  "body": check.status_comment_body(HEAD, "main", "in_progress", owner=owner)}
                        namespace = "issues" if kind == "issue_comment" else "pulls"
                        trigger = f"repos/{REPO}/{namespace}/comments/42"
                        rules = [history_rule(current_run_only()), comments_rule(ok(pages(findings))),
                                 patch_rule(ok("{}")), status_list_rule(ok(pages([status]))), status_patch_rule(ok("{}")),
                                 reaction_list_rule(ok(pages([reaction(70, "eyes")]))), reaction_delete_rule(70, ok("")),
                                 {"method": "GET", "path": trigger, "responses": [ok(json.dumps({"id":42,
                                  "issue_url" if namespace == "issues" else "pull_request_url":
                                  f"https://api.github.com/repos/{REPO}/{namespace}/7"}))]},
                                 {"method": "GET", "path": trigger + "/reactions", "responses": [ok(pages([reaction(71, "eyes")]))]},
                                 {"method": "DELETE", "path": trigger + "/reactions/71", "responses": [ok("")]}]
                        if desired:
                            rules.extend([reaction_post_rule(ok('{"id":80}')),
                                          {"method": "POST", "path": trigger + "/reactions", "responses": [ok('{"id":81}')]}])
                        harness.before.write_text("[]")
                        result = harness.run_script(["finalize"], rules, **{
                            "CHECK_RUN_ID":"99", "REVIEW_RESULT":"success", "ACTION_CONCLUSION":"success",
                            "BEFORE_IDS_FILE":str(harness.before), "STATUS_COMMENTS_ENABLED":"true",
                            "PR_REACTION_MODE":"manual", "PRESENTATION_OWNER":json.dumps(owner), **changes})
                        self.assertEqual(result.returncode, 1 if "FINDING_PUBLICATION" in changes else 0, result.stdout + result.stderr)
                        self.assertEqual(harness.calls("PATCH")[0]["body"]["conclusion"], conclusion)
                        self.assertEqual({call["path"] for call in harness.calls("DELETE")},
                                         {f"repos/{REPO}/issues/7/reactions/70", trigger + "/reactions/71"})
                        self.assertEqual([call["body"] for call in harness.calls("POST")],
                                         [{"content":desired}, {"content":desired}] if desired else [])
                    finally:
                        harness.tearDown()

    def test_failed_check_creation_still_bootstraps_terminal_owner_and_cleans(self):
        from test_claude_review_check import (ScriptTestCase, status_list_rule, status_patch_rule,
            pages, ok, FAIL, reaction_list_rule, reaction_delete_rule)
        harness = ScriptTestCase()
        harness.setUp()
        try:
            old = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"},
                   "body":check.status_comment_body(HEAD,"main","success")}
            trigger = f"repos/{REPO}/issues/comments/42"
            rules = [{"method":"POST","path":f"repos/{REPO}/check-runs","responses":[FAIL]},
                     status_list_rule(ok(pages([old]))),status_patch_rule(ok("{}")),
                     reaction_list_rule(ok(pages([reaction(70,"+1")]))),reaction_delete_rule(70,ok("")),
                     {"method":"GET","path":trigger,"responses":[ok(json.dumps({"id":42,"issue_url":f"https://api.github.com/repos/{REPO}/issues/7"}))]},
                     {"method":"GET","path":trigger+"/reactions","responses":[ok(pages([reaction(71,"eyes")]))]},
                     {"method":"DELETE","path":trigger+"/reactions/71","responses":[ok("")]}]
            result = harness.run_script(["create"], rules, STATUS_COMMENTS_ENABLED="true",
                                        PR_REACTION_MODE="manual",TRIGGER_KIND="issue_comment",TRIGGER_COMMENT_ID="42")
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn("presentation_owner=",harness.output.read_text())
            status_body = harness.calls("PATCH")[0]["body"]["body"]
            self.assertEqual(check.status_state({"body":status_body}),"failure")
            self.assertFalse(check.status_owner_running({"body":status_body}))
            self.assertEqual(len(harness.calls("DELETE")),2)
            self.assertFalse(any("reactions" in call["path"] for call in harness.calls("POST")))
        finally:
            harness.tearDown()



class EmergencyOwnershipTests(unittest.TestCase):
    def fallback(self, **kwargs):
        from test_claude_review_workflows import ToolingFallbackScriptTests, MANUAL
        return ToolingFallbackScriptTests().run_fallback(MANUAL, **kwargs)

    def test_emergency_cleanup_uses_both_correct_manual_endpoints_and_keeps_owner(self):
        from test_claude_review_workflows import REPO as workflow_repo, pr_identity
        for kind, namespace, moved_base in ((kind, namespace, moved)
                for kind, namespace in (("issue_comment", "issues"), ("pull_request_review_comment", "pulls"))
                for moved in (False, True)):
            with self.subTest(kind=kind, moved_base=moved_base):
                owner = presentation_owner(repo=workflow_repo, kind=kind, target=42)
                status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"},
                          "body":check.status_comment_body(HEAD, "main", "in_progress", owner=owner)}
                result, calls = self.fallback(OWNER_FIXTURE=owner, PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={
                    "STATUS":[{"stdout":json.dumps(status)}], "PR":[{"stdout":pr_identity().replace(BASE_TIP, OTHER) if moved_base else pr_identity()}],
                    "TRIGGER":[{"stdout":json.dumps({"id":42,
                        "issue_url" if namespace == "issues" else "pull_request_url":
                        f"https://api.github.com/repos/{workflow_repo}/{namespace}/7"})}],
                    "REACTIONS":[{"stdout":json.dumps([reaction(70,"eyes"), reaction(71,"+1",login="claude[bot]"),reaction(72,"heart")])}]})
                self.assertEqual(result.returncode, 1, result.stderr)
                deletes = [call for call in calls if call["method"] == "DELETE"]
                self.assertEqual({next(arg for arg in call["args"] if arg.startswith("repos/")) for call in deletes},
                                 {f"repos/{workflow_repo}/issues/7/reactions/70",
                                  f"repos/{workflow_repo}/{namespace}/comments/42/reactions/70"})
                repaired = next(call["body"]["body"] for call in calls if call["method"] == "PATCH"
                                and any("issues/comments/55" in arg for arg in call["args"]))
                self.assertEqual(check.status_owner({"body":repaired}), owner)
                self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_automatic_emergency_base_tip_cleanup_preserves_terminal_history(self):
        from test_claude_review_workflows import ToolingFallbackScriptTests, AUTOMATIC, REPO as workflow_repo, pr_identity
        for state in ("in_progress", "success"):
            with self.subTest(state=state):
                owner = presentation_owner(repo=workflow_repo)
                status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"},
                          "body":check.status_comment_body(HEAD, "main", state, owner=owner)}
                result, calls = ToolingFallbackScriptTests().run_fallback(AUTOMATIC,
                    OWNER_FIXTURE=owner, PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={
                        "STATUS":[{"stdout":json.dumps(status)}],
                        "PR":[{"stdout":pr_identity().replace(BASE_TIP, OTHER)}],
                        "REACTIONS":[{"stdout":json.dumps([reaction(70,"eyes")])}]})
                self.assertEqual(result.returncode, 1, result.stderr)
                writes = [call for call in calls if call["method"] in ("DELETE", "PATCH", "POST")
                          and not any("check-runs" in arg for arg in call["args"])]
                if state == "success":
                    self.assertEqual(writes, [])
                else:
                    self.assertTrue(any(call["method"] == "DELETE" for call in writes))
                    self.assertTrue(any(call["method"] == "PATCH" for call in writes))
                    self.assertFalse(any(call["method"] == "POST" for call in writes))

    def test_emergency_partial_rerun_cannot_claim_ownership(self):
        result, calls = self.fallback(PR_NUMBER="7", STATUS_COMMENT_ID="55", GITHUB_RUN_ATTEMPT="2")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([call["method"] for call in calls], ["PATCH"])

    def test_missing_emergency_owner_cannot_bootstrap_or_clear(self):
        result, calls = self.fallback(PR_NUMBER="7", STATUS_COMMENT_ID="55", PRESENTATION_OWNER="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([call["method"] for call in calls], ["PATCH"])

    def test_legacy_emergency_status_cannot_authorize_repair(self):
        legacy = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"}, "body":check.STATUS_MARKER}
        result, calls = self.fallback(PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={"STATUS":[{"stdout":json.dumps(legacy)}]})
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(call["method"] == "DELETE" for call in calls))
        self.assertEqual(len([call for call in calls if call["method"] == "PATCH"]), 1)

    def test_newer_owner_blocks_old_emergency_cleanup(self):
        from test_claude_review_workflows import REPO as workflow_repo
        newer = presentation_owner(repo=workflow_repo, run=6, started=3)
        status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"},
                  "body":check.status_comment_body(HEAD,"main","in_progress",owner=newer)}
        result, calls = self.fallback(PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={"STATUS":[{"stdout":json.dumps(status)}]})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len([call for call in calls if call["method"] == "PATCH"]), 1)
        self.assertFalse(any(call["method"] == "DELETE" for call in calls))

    def test_ambiguous_or_missing_status_markers_cannot_authorize_emergency_repair(self):
        from test_claude_review_workflows import REPO as workflow_repo
        owner = presentation_owner(repo=workflow_repo)
        valid = check.status_comment_body(HEAD,"main","in_progress",owner=owner)
        for body in (valid + "\n" + check.owner_marker(owner), valid.replace(check.STATUS_MARKER,"")):
            with self.subTest(body=body):
                status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"}, "body":body}
                result, calls = self.fallback(PR_NUMBER="7",STATUS_COMMENT_ID="55", responses={"STATUS":[{"stdout":json.dumps(status)}]})
                self.assertEqual(result.returncode,1)
                self.assertEqual(len([call for call in calls if call["method"]=="PATCH"]),1)
                self.assertFalse(any(call["method"]=="DELETE" for call in calls))

    def test_unverifiable_trigger_does_not_block_opening_cleanup(self):
        from test_claude_review_workflows import REPO as workflow_repo, pr_identity
        owner = presentation_owner(repo=workflow_repo, kind="issue_comment",target=42)
        result, calls = self.fallback(OWNER_FIXTURE=owner, PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={
            "PR":[{"stdout":pr_identity()}], "TRIGGER":[{"fail":True}],
            "REACTIONS":[{"stdout":json.dumps([reaction(70,"eyes")])}]})
        self.assertEqual(result.returncode, 1)
        deletes = [call for call in calls if call["method"] == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertIn(f"repos/{workflow_repo}/issues/7/reactions/70", deletes[0]["args"])


if __name__ == "__main__":
    unittest.main()
