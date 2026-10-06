"""Stateful offline tests for trusted request ownership and both reaction surfaces."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
import unittest
from unittest import mock

from test_claude_review_check import check, HEAD, OTHER, NEWER, BASE_TIP, MERGE_BASE, REPO, reaction, presentation_owner


class PresentationAPI:
    def __init__(self):
        self.status = None
        self.pr = {"head": {"sha": HEAD}, "base": {
            "sha": BASE_TIP, "ref": "main", "repo": {"full_name": REPO}}}
        self.base_tip = BASE_TIP
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
        elif path.startswith(f"repos/{REPO}/git/ref/heads/"):
            branch = check.unquote(path.split("/git/ref/heads/", 1)[1])
            result = json.dumps({"ref": f"refs/heads/{branch}",
                                 "object": {"type": "commit", "sha": self.base_tip}})
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
                self.api.base_tip = OTHER
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
        self.api.base_tip = OTHER
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.api.status, previous)
        self.assertEqual(self.owned(path), ["+1"])
        self.start(GITHUB_RUN_ID="6", PRESENTATION_START="3")
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", "success"))
        self.assertEqual(check.status_owner(self.api.status)["run"], 6)
        self.assertEqual(self.owned(self.opening), [])
        self.assertEqual(self.owned(path), [])

    def test_non_completed_base_advance_preserves_only_previous_review_evidence(self):
        for previous in (False, True):
            for state, available in (("failure", True), ("publication_incomplete", False), ("success", False)):
                with self.subTest(previous=previous, state=state, available=available):
                    self.api = PresentationAPI()
                    self.gh_mock.side_effect = self.api.call
                    os.environ.update(GITHUB_RUN_ID="5", PRESENTATION_START="2",
                                      DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                    if previous:
                        self.start()
                        self.finish()
                    self.start(GITHUB_RUN_ID="6", PRESENTATION_START="3",
                               DETAILS_URL="https://github.com/owner/repo/actions/runs/6")
                    self.api.base_tip = OTHER
                    self.finish(state, available)
                    self.assertEqual(check.status_state(self.api.status), "stale")
                    self.assertEqual(check.last_completed_review(self.api.status),
                                     (HEAD, "main", "success") if previous else None)
                    self.assertEqual(check.last_completed_review_details(self.api.status),
                                     {"base_sha": BASE_TIP, "run_url": "https://github.com/owner/repo/actions/runs/5"}
                                     if previous else {})
                    self.assertEqual(self.owned(self.opening), [])

    def test_new_base_owner_keeps_historical_metadata_and_blocks_old_finalizer(self):
        old_url = "https://github.com/owner/repo/actions/runs/5"
        old = self.start(DETAILS_URL=old_url)
        self.finish()
        self.api.base_tip = OTHER
        self.api.pr["base"]["sha"] = OTHER
        newer = self.start(GITHUB_RUN_ID="6", PRESENTATION_START="3", BASE_SHA=OTHER,
                           DETAILS_URL="https://github.com/owner/repo/actions/runs/6")
        self.finish("failure")
        self.assertEqual(check.last_completed_review_details(self.api.status), {"base_sha": BASE_TIP, "run_url": old_url})
        self.assertEqual(check.status_owner(self.api.status), newer)
        previous = copy.deepcopy(self.api.status)
        reactions = copy.deepcopy(self.api.reactions)
        self.api.calls.clear()
        os.environ.update(PRESENTATION_OWNER=json.dumps(old), GITHUB_RUN_ID="5", BASE_SHA=BASE_TIP,
                          DETAILS_URL=old_url)
        self.finish()
        self.assertEqual(self.api.status, previous)
        self.assertEqual(self.api.reactions, reactions)
        self.assertEqual(self.writes(), [])
        os.environ["DETAILS_URL"] = "https://github.com/owner/repo/actions/runs/7"
        self.api.base_tip = MERGE_BASE
        check.cmd_stale()
        self.assertEqual(check.last_completed_review_details(self.api.status), {"base_sha": BASE_TIP, "run_url": old_url})
        self.assertIn(f"**Reviewed baseline:** `{BASE_TIP[:7]}`", self.api.status["body"])
        self.assertIn(f"[Reviewed workflow run]({old_url})", self.api.status["body"])

    def test_legacy_review_does_not_inherit_current_base_or_run_metadata(self):
        with mock.patch.dict(os.environ, BASE_SHA="", DETAILS_URL=""):
            # Older runtime comments lack the newly stored historical metadata.
            self.api.status = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                               "body": check.status_comment_body(OTHER, "release", "success")}
        self.assertEqual(check.last_completed_review_details(self.api.status), {})
        self.start(DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
        self.finish("failure")
        self.api.base_tip = OTHER
        check.cmd_stale()
        self.assertEqual(check.last_completed_review(self.api.status), (OTHER, "release", "success"))
        self.assertEqual(check.last_completed_review_details(self.api.status), {})
        self.assertNotIn("**Reviewed baseline:**", self.api.status["body"])
        self.assertNotIn("[Reviewed workflow run]", self.api.status["body"])

    def test_direct_tip_advancement_ignores_stale_pr_projection(self):
        self.target("issue_comment")
        self.start()
        self.api.base_tip = OTHER
        self.finish()
        self.assertEqual(self.api.pr["base"]["sha"], BASE_TIP)
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertEqual(self.owned(self.opening), [])

    def test_same_head_status_refresh_observes_direct_base_movement(self):
        trigger = self.target("pull_request_review_comment")
        owner = self.start()
        self.finish()
        self.api.base_tip = OTHER
        check.cmd_stale()
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertEqual(check.status_owner(self.api.status), owner)
        self.assertEqual(self.owned(self.opening), [])
        # The completed trigger remains valid historical information.
        self.assertEqual(self.owned(trigger), ["+1"])

    def test_missing_direct_ref_cannot_add_terminal_clean_presentation(self):
        trigger = self.target("issue_comment")
        self.start()
        previous = copy.deepcopy(self.api.status)
        self.api.faults[("GET", f"repos/{REPO}/git/ref/heads/main")] = "fail"
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.api.status, previous)
        self.assertEqual(self.writes(), [])
        self.assertNotIn("+1", self.owned(self.opening))
        self.assertNotIn("+1", self.owned(trigger))

    def test_projection_disagreement_cannot_claim_fresh_terminal_presentation(self):
        self.start()
        previous = copy.deepcopy(self.api.status)
        self.api.pr["base"]["sha"] = OTHER
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.api.status, previous)
        self.assertEqual(self.writes(), [])

    def test_retarget_during_direct_ref_read_suppresses_presentation(self):
        self.start()
        previous = copy.deepcopy(self.api.status)
        self.api.calls.clear()
        def retarget(method, path):
            if "/git/ref/heads/" in path:
                self.api.pr["base"]["ref"] = "release"
        self.api.on_call = retarget
        self.finish()
        self.assertEqual(self.api.status, previous)
        self.assertEqual(self.writes(), [])

    def test_slash_branch_tip_is_read_through_exact_encoded_endpoint(self):
        self.api.pr["base"]["ref"] = "release/2026"
        self.start(BASE_REF="release/2026")
        self.finish()
        self.assertIn(("GET", f"repos/{REPO}/git/ref/heads/release%2F2026", None), self.api.calls)
        self.assertEqual(self.owned(self.opening), ["+1"])

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
        self.assertEqual(self.output, [("presentation_outcome", "unavailable")])
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

    def test_still_owned_superseded_attempt_marks_shared_status_stale_and_cleans(self):
        path = self.target("issue_comment")
        self.start()
        self.api.pr["head"]["sha"] = OTHER
        self.api.calls.clear()
        self.finish()
        self.assertEqual(self.owned(path), [])
        self.assertEqual(self.owned(self.opening), [])
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertEqual(check.status_head(self.api.status), OTHER)
        self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", "success"))

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

    def test_empty_owner_diagnostics_do_not_read_or_acquire_presentation(self):
        cases = [("", "::warning::", "unknown outcome"),
                 ("unavailable", "::warning::", "unavailable"),
                 ("acquired", "::warning::", "no owner receipt"),
                 ("suppressed_patch_changed", "::notice::", "captured head/base was already superseded"),
                 ("suppressed_newer_owner", "::notice::", "a newer attempt already owned presentation"),
                 ("suppressed_presentation_changed", "::notice::", "presentation changed before publication"),
                 ("untrusted diagnostic text", "::warning::", "unknown outcome")]
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for outcome, severity, reason in cases:
                with self.subTest(kind=kind, outcome=outcome), mock.patch.dict(
                        os.environ, PRESENTATION_OWNER="", PRESENTATION_OUTCOME=outcome, TRIGGER_KIND=kind):
                    self.api.calls.clear()
                    self.output.clear()
                    log = io.StringIO()
                    with redirect_stdout(log):
                        self.finish()
                        self.finish()
                    self.assertIn(severity, log.getvalue())
                    self.assertIn(reason, log.getvalue())
                    self.assertNotIn("Expecting value", log.getvalue())
                    self.assertNotIn("untrusted diagnostic text", log.getvalue())
                    self.assertEqual(self.api.calls, [])
                    self.assertEqual(self.output, [])
                    self.assertIsNone(check.PRESENTATION_DEADLINE)

    def test_malformed_owner_diagnostics_never_touch_presentation_api(self):
        for receipt in (" ", "{", "[]", "null", "{}", json.dumps({**presentation_owner(), "generation": "0" * 64})):
            with self.subTest(receipt=receipt), mock.patch.dict(os.environ, PRESENTATION_OWNER=receipt):
                self.api.calls.clear()
                log = io.StringIO()
                with redirect_stdout(log):
                    self.finish()
                self.assertIn("::warning::", log.getvalue())
                self.assertIn("malformed owner receipt", log.getvalue())
                self.assertEqual(self.api.calls, [])

    def test_start_api_failures_export_unavailable_without_authority(self):
        for method, path in (("GET", f"repos/{REPO}/issues/7/comments"),
                             ("GET", f"repos/{REPO}/git/ref/heads/main"),
                             ("POST", f"repos/{REPO}/issues/7/comments")):
            with self.subTest(method=method, path=path):
                self.api = PresentationAPI()
                self.gh_mock.side_effect = self.api.call
                self.api.faults[(method, path)] = "fail"
                self.output.clear()
                log = io.StringIO()
                with redirect_stdout(log):
                    self.start()
                self.assertIn("::warning::", log.getvalue())
                self.assertIn(("presentation_outcome", "unavailable"), self.output)
                self.assertFalse(any(name == "presentation_owner" for name, _ in self.output))
                self.assertEqual(self.owned(self.opening), [])

    def test_superseded_start_exports_notice_and_preserves_new_owner(self):
        newer = self.start(GITHUB_RUN_ID="6", PRESENTATION_START="3")
        previous = copy.deepcopy(self.api.status)
        reactions = copy.deepcopy(self.api.reactions)
        self.api.calls.clear()
        self.output.clear()
        log = io.StringIO()
        with redirect_stdout(log):
            self.start(GITHUB_RUN_ID="5", PRESENTATION_START="2")
        self.assertIn("::notice::", log.getvalue())
        self.assertEqual(self.output, [("presentation_outcome", "suppressed_newer_owner")])
        self.assertEqual(self.api.status, previous)
        self.assertEqual(check.status_owner(self.api.status), newer)
        self.assertEqual(self.api.reactions, reactions)
        self.assertEqual(self.writes(), [])

    def test_changed_status_before_acquisition_exports_suppression(self):
        newer = presentation_owner(run=6, started=3)
        reads = 0
        def change(method, path):
            nonlocal reads
            if method == "GET" and path == f"repos/{REPO}/issues/7/comments":
                reads += 1
                if reads == 2:
                    self.api.status = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                                       "body": check.status_comment_body(HEAD, "main", "in_progress", owner=newer)}
        self.api.on_call = change
        log = io.StringIO()
        with redirect_stdout(log):
            self.start()
        self.assertIn("::notice::", log.getvalue())
        self.assertEqual(self.output, [("presentation_outcome", "suppressed_presentation_changed")])
        self.assertEqual(check.status_owner(self.api.status), newer)
        self.assertEqual(self.writes(), [])

    def test_late_patch_change_creates_visible_stale_status(self):
        for field in ("head", "base"):
            with self.subTest(field=field):
                self.api = PresentationAPI()
                self.gh_mock.side_effect = self.api.call
                self.output.clear()
                reads = 0
                def change(method, path):
                    nonlocal reads
                    if method == "GET" and path == f"repos/{REPO}/issues/7/comments":
                        reads += 1
                        if reads == 2:
                            self.api.pr[field]["sha"] = OTHER
                            if field == "base":
                                self.api.base_tip = OTHER
                self.api.on_call = change
                log = io.StringIO()
                with redirect_stdout(log):
                    self.start()
                self.assertEqual(check.status_state(self.api.status), "stale")
                self.assertEqual(check.status_owner(self.api.status)["head"], HEAD)
                self.assertEqual(check.status_owner(self.api.status)["base"], BASE_TIP)
                self.assertEqual(dict(self.output)["presentation_outcome"], "acquired")
                self.assertEqual(self.owned(self.opening), [])
                self.assertEqual(len([call for call in self.writes() if call[0] == "POST"
                                      and call[1].endswith("/comments")]), 1)

    def test_absent_persisted_owner_is_unavailable_not_supersession(self):
        os.environ["PRESENTATION_OWNER"] = json.dumps(presentation_owner())
        log = io.StringIO()
        with redirect_stdout(log):
            self.finish()
        self.assertIn("::warning::", log.getvalue())
        self.assertIn("persisted owner is missing or malformed", log.getvalue())
        self.assertEqual(self.writes(), [])

    def test_output_failure_remains_best_effort_and_resets_deadline(self):
        log = io.StringIO()
        with mock.patch.object(check, "write_output", side_effect=OSError("injected output failure")), redirect_stdout(log):
            self.start()
        self.assertIn("Could not export the Claude Review presentation diagnostic", log.getvalue())
        self.assertIsNone(check.PRESENTATION_DEADLINE)
        self.assertEqual(self.output, [])
        self.assertEqual(self.owned(self.opening), [])

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

    def test_stale_at_start_persists_captured_patch_and_reuses_one_comment(self):
        for moved in ("head", "base", "ref"):
            with self.subTest(moved=moved):
                self.api = PresentationAPI()
                self.gh_mock.side_effect = self.api.call
                self.output.clear()
                os.environ.update(GITHUB_RUN_ID="5", GITHUB_RUN_ATTEMPT="1", PRESENTATION_START="2")
                if moved == "head":
                    self.api.pr["head"]["sha"] = OTHER
                elif moved == "base":
                    self.api.base_tip = OTHER
                else:
                    self.api.pr["base"]["ref"] = "release"
                owner = self.start()
                self.assertEqual(check.status_state(self.api.status), "stale")
                self.assertEqual((owner["head"], owner["base"], owner["base_ref"]), (HEAD, BASE_TIP, "main"))
                self.assertIn("superseded", self.api.status["body"])
                self.assertIn(f"**Captured baseline:** `{BASE_TIP[:7]}`", self.api.status["body"])
                self.assertIn("/actions/runs/5", self.api.status["body"])
                self.assertEqual(self.owned(self.opening), [])
                self.start()  # Retry does not recreate/reset the existing stale owner.
                self.assertEqual(len([call for call in self.writes() if call[0] == "POST"
                                      and call[1].endswith("/comments")]), 1)
                self.finish("failure")
                self.assertEqual(check.status_state(self.api.status), "stale")

    def test_cancelled_review_has_visible_reason_and_no_success_reaction(self):
        for moved in (False, True):
            with self.subTest(moved=moved):
                self.api = PresentationAPI()
                self.gh_mock.side_effect = self.api.call
                path = self.target("issue_comment")
                self.start()
                if moved:
                    self.api.pr["head"]["sha"] = OTHER
                os.environ["REVIEW_RESULT"] = "cancelled"
                self.finish("failure")
                self.assertEqual(check.status_state(self.api.status), "stale" if moved else "failure")
                self.assertIn("cancelled before verified completion", self.api.status["body"])
                self.assertEqual(self.owned(self.opening) + self.owned(path), [])
                self.assertEqual(len([call for call in self.writes() if call[0] == "POST"
                                      and call[1].endswith("/comments")]), 1)

    def test_terminal_reason_survives_movement_and_repeated_stale_refreshes(self):
        cases = (("failure", {"REVIEW_RESULT":"cancelled"}, "cancelled"),
                 ("failure", {"REVIEW_RESULT":"failure"}, "incomplete"),
                 ("failure", {"START_RESULT":"failure"}, "preparation_failed"),
                 ("publication_incomplete", {}, "check_publication_failed"))
        for state, changes, reason_key in cases:
            for moved_before_finish in (False, True):
                with self.subTest(state=state, reason=reason_key, moved_before_finish=moved_before_finish):
                    self.api = PresentationAPI()
                    self.gh_mock.side_effect = self.api.call
                    os.environ.update(REVIEW_RESULT="success", START_RESULT="success", HEAD_SHA=HEAD)
                    owner = self.start()
                    if moved_before_finish:
                        self.api.pr["head"]["sha"] = OTHER
                    os.environ.update(changes)
                    self.finish(state)
                    self.assertIn(check.STATUS_REASONS[reason_key], self.api.status["body"])
                    self.api.pr["head"]["sha"] = OTHER
                    with mock.patch.dict(os.environ, HEAD_SHA=OTHER):
                        for _ in range(3):
                            check.cmd_stale()
                            self.assertIn(check.STATUS_REASONS[reason_key], self.api.status["body"])
                            self.assertEqual(check.status_state(self.api.status), "stale")
                            self.assertEqual(check.status_owner(self.api.status), owner)
                            self.assertFalse(check.status_owner_running(self.api.status))
                            self.assertEqual(self.api.status["body"].count("**Reason:**"),1)
                            self.assertEqual(self.api.status["body"].count(check.STATUS_REASONS["stale"]),1)
                    self.assertEqual(self.owned(self.opening), [])
                    self.assertEqual(len([call for call in self.writes() if call[0] == "POST"
                                          and call[1].endswith("/comments")]),1)

    def test_two_head_movements_during_finalization_preserve_terminal_reason_on_refresh(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for result, reason_key in (("cancelled", "cancelled"), ("failure", "incomplete")):
                with self.subTest(kind=kind, result=result):
                    self.api = PresentationAPI()
                    self.gh_mock.side_effect = self.api.call
                    os.environ.update(TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic",
                                      TRIGGER_COMMENT_ID="", REVIEW_RESULT=result, START_RESULT="success", HEAD_SHA=HEAD)
                    trigger = self.target(kind) if kind != "automatic" else None
                    owner = self.start()
                    self.api.pr["head"]["sha"] = OTHER
                    reads = 0
                    def move_again(method, path):
                        nonlocal reads
                        if method == "GET" and path == f"repos/{REPO}/issues/7/comments":
                            reads += 1
                            if reads == 2:
                                self.api.pr["head"]["sha"] = NEWER
                    self.api.on_call = move_again
                    self.finish("failure")
                    self.api.on_call = None
                    expected = check.STATUS_REASONS["stale"] + " " + check.STATUS_REASONS[reason_key]
                    self.assertGreaterEqual(reads,2)
                    self.assertEqual(check.status_head(self.api.status),NEWER)
                    self.assertEqual(check.terminal_status_reason(self.api.status,owner),expected)
                    with mock.patch.dict(os.environ,HEAD_SHA=NEWER):
                        for _ in range(3):
                            check.cmd_stale()
                            self.assertEqual(check.terminal_status_reason(self.api.status,owner),expected)
                            self.assertEqual(self.api.status["body"].count(check.STATUS_REASONS["stale"]),1)
                            self.assertEqual(self.api.status["body"].count(check.STATUS_REASONS[reason_key]),1)
                            self.assertEqual(check.status_owner(self.api.status),owner)
                            self.assertFalse(check.status_owner_running(self.api.status))
                    self.assertEqual(self.owned(self.opening),[])
                    if trigger:
                        self.assertEqual(self.owned(trigger),[])

    def test_new_acquisition_does_not_inherit_previous_terminal_reason(self):
        self.start()
        os.environ["REVIEW_RESULT"] = "cancelled"
        self.finish("failure")
        self.assertIn(check.STATUS_REASONS["cancelled"],self.api.status["body"])
        self.api.base_tip = OTHER
        newer = self.start(GITHUB_RUN_ID="6", PRESENTATION_START="3")
        self.assertEqual(check.status_state(self.api.status),"stale")
        self.assertNotIn(check.STATUS_REASONS["cancelled"],self.api.status["body"])
        check.cmd_stale()
        self.assertNotIn(check.STATUS_REASONS["cancelled"],self.api.status["body"])
        self.assertEqual(check.status_owner(self.api.status),newer)

    def test_stale_refresh_does_not_carry_arbitrary_or_ambiguous_reason_copy(self):
        for corruption in ("arbitrary", "duplicate", "running"):
            with self.subTest(corruption=corruption):
                self.api = PresentationAPI()
                self.gh_mock.side_effect = self.api.call
                os.environ.update(REVIEW_RESULT="cancelled", START_RESULT="success", HEAD_SHA=HEAD)
                self.start()
                self.finish("failure")
                original = copy.deepcopy(self.api.status)
                self.assertIsNone(check.terminal_status_reason(original,presentation_owner(run=6,started=3)))
                if corruption == "arbitrary":
                    self.api.status["body"] = self.api.status["body"].replace(
                        check.STATUS_REASONS["cancelled"],"Untrusted terminal assertion.")
                elif corruption == "duplicate":
                    self.api.status["body"] += "\n**Reason:** " + check.STATUS_REASONS["cancelled"]
                else:
                    self.api.status["body"] = self.api.status["body"].replace(
                        check.OWNER_PHASE_PREFIX+"terminal -->",check.OWNER_PHASE_PREFIX+"running -->")
                self.api.pr["head"]["sha"] = OTHER
                with mock.patch.dict(os.environ,HEAD_SHA=OTHER):
                    check.cmd_stale()
                self.assertNotIn("Untrusted terminal assertion",self.api.status["body"])
                self.assertNotIn(check.STATUS_REASONS["cancelled"],self.api.status["body"])
                self.assertIn(check.STATUS_REASONS["stale"],self.api.status["body"])

    def test_stale_refresh_links_its_owner_run_without_current_run_substitution(self):
        self.start()
        self.api.pr["head"]["sha"] = OTHER
        with mock.patch.dict(os.environ, HEAD_SHA=OTHER, DETAILS_URL="https://github.com/owner/repo/actions/runs/99"):
            check.cmd_stale()
        self.assertIn(f"[Attempt workflow run](https://github.com/{REPO}/actions/runs/5)", self.api.status["body"])
        self.assertNotIn("/actions/runs/99", self.api.status["body"])


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

    def test_status_api_failure_is_recorded_in_check_without_changing_review_evidence(self):
        from test_claude_review_check import (ScriptTestCase, history_rule, current_run_only,
            comments_rule, comment, patch_rule, status_list_rule, status_patch_rule, pages, ok, FAIL,
            reaction_list_rule, reaction_delete_rule)
        for failure, findings in ((failure, findings) for failure in
                ("write", "read", "start", "missing", "malformed", "newer_owner")
                for findings in ([], [comment(9)])):
            with self.subTest(failure=failure, findings=bool(findings)):
                harness = ScriptTestCase()
                harness.setUp()
                try:
                    owner = presentation_owner()
                    status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"},
                              "body":check.status_comment_body(HEAD,"main","in_progress",owner=owner)}
                    if failure == "malformed":
                        status["body"] = status["body"].replace(check.owner_marker(owner), check.OWNER_PREFIX + "invalid -->")
                    elif failure == "newer_owner":
                        newer = presentation_owner(run=6, started=owner["started"] + 1)
                        status["body"] = check.status_comment_body(HEAD,"main","in_progress",owner=newer)
                    statuses = [] if failure == "missing" else [status]
                    rules = [history_rule(current_run_only()), comments_rule(ok(pages(findings))), patch_rule(ok("{}")),
                             status_list_rule(FAIL if failure == "read" else ok(pages(statuses))),
                             status_patch_rule(FAIL if failure == "write" else ok("{}")),
                             reaction_list_rule(ok(pages([reaction(70,"eyes")]))), reaction_delete_rule(70,ok(""))]
                    harness.before.write_text("[]")
                    result = harness.run_script(["finalize"], rules, CHECK_RUN_ID="99", REVIEW_RESULT="success",
                        ACTION_CONCLUSION="success", BEFORE_IDS_FILE=str(harness.before), STATUS_COMMENTS_ENABLED="true",
                        PRESENTATION_OWNER="" if failure == "start" else json.dumps(owner),
                        PRESENTATION_OUTCOME="unavailable" if failure == "start" else "acquired")
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    checks = [call["body"] for call in harness.calls("PATCH") if "/check-runs/" in call["path"]]
                    self.assertEqual(len(checks),1 if failure == "newer_owner" else 2)
                    if failure in ("missing", "malformed", "newer_owner"):
                        self.assertFalse(any("/reactions" in call["path"] for call in harness.calls()))
                        self.assertFalse(any("/issues/comments/" in call["path"] for call in harness.calls("PATCH")))
                    self.assertEqual(checks[-1]["conclusion"],"action_required" if findings else "success")
                    self.assertEqual(checks[-1]["output"]["text"],checks[0]["output"]["text"])
                    diagnostic = "shared status comment could not be updated"
                    if failure == "newer_owner":
                        self.assertNotIn(diagnostic,checks[-1]["output"]["summary"])
                    else:
                        self.assertIn(diagnostic,checks[-1]["output"]["summary"])
                    self.assertLessEqual(len(checks[-1]["output"]["summary"].encode()),check.CHECK_SUMMARY_BYTES)
                    if failure == "start":
                        self.assertIn("unavailable at review start",checks[-1]["output"]["summary"])
                    self.assertFalse(harness.calls("POST"))
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
                    "STATUS":[{"stdout":json.dumps(status)}], "PR":[{"stdout":pr_identity()}],
                    "REF":[{"stdout":json.dumps({"ref":"refs/heads/main", "object":{
                        "type":"commit", "sha":OTHER if moved_base else BASE_TIP}})}],
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
                        "PR":[{"stdout":pr_identity()}],
                        "REF":[{"stdout":json.dumps({"ref":"refs/heads/main", "object":{
                            "type":"commit", "sha":OTHER}})}],
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

    def test_emergency_known_retarget_marks_shared_comment_stale(self):
        from test_claude_review_workflows import ToolingFallbackScriptTests, AUTOMATIC, MANUAL, pr_identity
        for workflow in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=workflow.name):
                result, calls = ToolingFallbackScriptTests().run_fallback(workflow,
                    PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={
                        "PR":[{"stdout":pr_identity(OTHER,"release")}],
                        "REF":[{"stdout":json.dumps({"ref":"refs/heads/release", "object":{
                            "type":"commit", "sha":OTHER}})}]})
                self.assertEqual(result.returncode,1,result.stderr)
                repaired = next(call["body"]["body"] for call in calls if call["method"] == "PATCH"
                                and any("issues/comments/55" in arg for arg in call["args"]))
                self.assertEqual(check.status_state({"body":repaired}),"stale")
                self.assertEqual(check.status_head({"body":repaired}),OTHER)
                self.assertEqual(check.status_base_ref({"body":repaired}),"release")
                self.assertEqual(check.status_owner({"body":repaired})["head"],HEAD)
                self.assertFalse(any(call["method"] == "POST" for call in calls))

    def test_emergency_stale_renders_only_valid_historical_metadata(self):
        from test_claude_review_workflows import ToolingFallbackScriptTests, AUTOMATIC, MANUAL, REPO as workflow_repo, pr_identity
        from urllib.parse import quote
        reviewed_head, reviewed_base = "a" * 40, "b" * 40
        reviewed_url = f"https://github.com/{workflow_repo}/actions/runs/9"
        for workflow in (AUTOMATIC, MANUAL):
            for result_state in ("success", "action_required"):
                for metadata in ("complete", "legacy", "invalid_optional", "invalid_required", "duplicate_required"):
                    with self.subTest(workflow=workflow.name, result=result_state, metadata=metadata):
                        owner = presentation_owner(repo=workflow_repo)
                        details = {"base_sha": reviewed_base, "run_url": reviewed_url} if metadata == "complete" else {}
                        body = check.status_comment_body(HEAD,"main","in_progress",owner=owner,
                            last_review=(reviewed_head,"release/old",result_state),last_review_details=details)
                        if metadata == "invalid_optional":
                            body += "\n" + check.STATUS_REVIEWED_BASE_SHA_PREFIX + "invalid -->"
                            body += "\n" + check.STATUS_REVIEWED_RUN_URL_PREFIX + quote("javascript:alert(1)",safe="") + " -->"
                        elif metadata == "invalid_required":
                            body = body.replace(check.STATUS_REVIEWED_HEAD_PREFIX + reviewed_head,
                                                check.STATUS_REVIEWED_HEAD_PREFIX + "invalid")
                        elif metadata == "duplicate_required":
                            body += "\n" + check.STATUS_REVIEWED_HEAD_PREFIX + reviewed_head + " -->"
                        status = {"id":55,"user":{"login":check.STATUS_AUTHOR,"type":"Bot"},"body":body}
                        run, calls = ToolingFallbackScriptTests().run_fallback(workflow,
                            OWNER_FIXTURE=owner,PR_NUMBER="7",STATUS_COMMENT_ID="55",responses={
                                "STATUS":[{"stdout":json.dumps(status)}],
                                "PR":[{"stdout":pr_identity(OTHER)}]})
                        self.assertEqual(run.returncode,1,run.stderr)
                        repaired = next(c["body"]["body"] for c in calls if c["method"] == "PATCH"
                                        and any("issues/comments/55" in a for a in c["args"]))
                        self.assertEqual(check.status_state({"body":repaired}),"stale")
                        self.assertEqual(check.status_owner({"body":repaired}),owner)
                        self.assertIn("not reviewed",repaired)
                        if metadata in ("invalid_required","duplicate_required"):
                            self.assertNotIn("**Last reviewed",repaired)
                            self.assertNotIn("[Reviewed workflow run]",repaired)
                        else:
                            label = "clean" if result_state == "success" else "findings"
                            self.assertIn(f"**Last reviewed (historical):** `{reviewed_head[:7]}` on `release/old`",repaired)
                            self.assertIn(label,repaired)
                            self.assertEqual("**Reviewed baseline:**" in repaired,metadata == "complete")
                            self.assertEqual("[Reviewed workflow run]" in repaired,metadata == "complete")
                            if metadata == "complete":
                                self.assertIn(f"**Reviewed baseline:** `{reviewed_base[:7]}`",repaired)
                                self.assertIn(f"[Reviewed workflow run]({reviewed_url})",repaired)
                        self.assertIn("[Attempt workflow run](https://example.invalid/run)",repaired)
                        self.assertNotIn("javascript:",repaired)
                        self.assertFalse(any(c["method"] == "POST" for c in calls))

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
