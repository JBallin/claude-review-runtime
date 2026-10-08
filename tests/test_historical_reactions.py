"""Offline historical reaction signals remain separate from current coverage."""
import os
import unittest

from test_claude_review_check import check, HEAD, OTHER, BASE_TIP, REPO, reaction
import test_presentation_ownership as ownership

DONE = "2026-10-07T13:09:19Z"


class HistoricalReactionTests(unittest.TestCase):
    setUp = ownership.OwnershipTests.setUp
    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target
    owned = ownership.OwnershipTests.owned
    writes = ownership.OwnershipTests.writes

    def reset(self, kind="automatic"):
        self.api = ownership.PresentationAPI()
        self.gh_mock.side_effect = self.api.call
        self.output.clear()
        os.environ.update(HEAD_SHA=HEAD, BASE_SHA=BASE_TIP, BASE_REF="main",
                          GITHUB_RUN_ID="5", PRESENTATION_START="2", REVIEW_RESULT="success",
                          TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic",
                          DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
        os.environ.pop("TRIGGER_COMMENT_ID", None)
        os.environ.pop("PRESENTATION_OWNER", None)
        return self.target(kind) if kind != "automatic" else None

    def move(self, field):
        if field == "head":
            self.api.pr["head"]["sha"] = OTHER
        elif field == "base":
            self.api.base_tip = OTHER
        else:
            self.api.pr["base"]["ref"] = "release"
            os.environ["BASE_REF"] = "release"

    def refresh(self):
        check.best_effort_status(self.api.pr["head"]["sha"], "stale")

    def test_completed_result_across_patch_movement_and_repeated_refresh(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for result in ("success", "action_required"):
                for field in ("head", "base", "ref"):
                    for during in (False, True):
                        with self.subTest(kind=kind, result=result, field=field, during=during):
                            trigger = self.reset(kind)
                            owner = self.start()
                            preserved = reaction(71, "heart", login="example-user")
                            self.api.reactions[self.opening].append(preserved)
                            if during:
                                self.move(field)
                                # Finalization retains the captured ref even after retargeting.
                                os.environ["BASE_REF"] = "main"
                            check.best_effort_status(HEAD, result, completed_at=DONE)
                            if not during:
                                self.move(field)
                            os.environ["BASE_REF"] = self.api.pr["base"]["ref"]
                            for _ in range(2):
                                self.refresh()
                                expected = ["+1"] if result == "success" else []
                                self.assertEqual(self.owned(self.opening), expected)
                                if trigger:
                                    self.assertEqual(self.owned(trigger), expected)
                                self.assertIn(preserved, self.api.reactions[self.opening])
                                self.assertEqual(check.status_owner(self.api.status), owner)
                                self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", result))
                                self.assertEqual(check.last_completed_review_details(self.api.status)["completed_at"], DONE)
                                self.assertEqual(check.status_state(self.api.status), "stale")
                                visible = self.api.status["body"].split("<details>")[0]
                                self.assertIn("| Status | Commit | Review trigger |", visible)
                                self.assertIn(check.relative_time(DONE), visible)
                                self.assertIn("Integration with the current baseline has not been reviewed."
                                              if field == "base" else "⚠️ This completed review doesn’t cover the current version."
                                              if result == "success" else "⚠️ These recorded findings are from a review of a previous version.", visible)
                            self.assertFalse(any("/check-runs" in path for _, path, _ in self.writes()))

    def test_newer_attempt_and_same_owner_failure_block_old_clean_signal(self):
        for state in ("in_progress", "action_required", "failure", "publication_incomplete", "cancelled", "same_owner_failure"):
            with self.subTest(state=state):
                trigger = self.reset("issue_comment")
                self.start()
                check.best_effort_status(HEAD, "success", completed_at=DONE)
                if state != "same_owner_failure":
                    self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3,
                               DETAILS_URL="https://github.com/owner/repo/actions/runs/6")
                if state != "in_progress":
                    if state == "cancelled":
                        os.environ["REVIEW_RESULT"] = "cancelled"
                    check.best_effort_status(HEAD, "failure" if state in ("cancelled", "same_owner_failure") else state,
                                             completed_at=DONE)
                self.move("head")
                for _ in range(2):
                    self.refresh()
                    expected = ["eyes"] if state == "in_progress" else []
                    self.assertEqual(self.owned(self.opening), expected)
                    self.assertEqual(self.owned(trigger), expected)

    def test_ambiguous_or_missing_completion_evidence_never_recovers_thumbs_up(self):
        corruptions = [
            lambda body: body.replace(check.STATUS_STATE_PREFIX + "success -->",
                                       check.STATUS_STATE_PREFIX + "action_required -->"),
            lambda body: body + "\n" + check.STATUS_REVIEWED_HEAD_PREFIX + OTHER + " -->",
            lambda body: body + "\n" + check.STATUS_STATE_PREFIX + "failure -->",
            lambda body: body + "\n**Reason:** unknown outcome",
            lambda body: body + "\n" + check.STATUS_REVIEWED_COMPLETED_PREFIX + DONE + " -->",
            lambda body: "\n".join(line for line in body.splitlines()
                                   if not line.startswith(check.STATUS_REVIEWED_RUN_URL_PREFIX)),
            lambda body: "\n".join(line for line in body.splitlines()
                                   if not line.startswith(check.STATUS_REVIEWED_BASE_SHA_PREFIX)),
        ]
        for index, corrupt in enumerate(corruptions):
            with self.subTest(index=index):
                trigger = self.reset("issue_comment")
                self.start()
                check.best_effort_status(HEAD, "success", completed_at=DONE)
                self.api.status["body"] = corrupt(self.api.status["body"])
                self.move("base")
                for _ in range(2):
                    self.refresh()
                    self.assertEqual(self.owned(self.opening), [])
                    self.assertEqual(self.owned(trigger), [])

    def test_unavailable_check_and_failed_status_write_do_not_add_thumbs_up(self):
        for failure in ("check", "status"):
            with self.subTest(failure=failure):
                trigger = self.reset("issue_comment")
                self.start()
                self.move("head")
                if failure == "status":
                    self.api.faults[("PATCH", f"repos/{REPO}/issues/comments/55")] = "fail"
                check.best_effort_status(HEAD, "success", check_available=failure != "check", completed_at=DONE)
                self.assertEqual(self.owned(self.opening), [])
                self.assertEqual(self.owned(trigger), [])
                self.api.faults.clear()
                self.refresh()
                self.assertNotIn("+1", self.owned(self.opening) + self.owned(trigger))

    def test_unavailable_check_on_current_patch_cannot_mint_clean_history(self):
        for result in ("success", "action_required"):
            with self.subTest(result=result):
                trigger = self.reset("issue_comment")
                self.start()
                check.best_effort_status(HEAD, result, check_available=False, completed_at=DONE)
                self.assertEqual(check.status_state(self.api.status), "publication_incomplete")
                self.assertIsNone(check.last_completed_review(self.api.status))
                self.assertNotIn(check.STATUS_REVIEWED_COMPLETED_PREFIX, self.api.status["body"])
                self.move("head")
                self.refresh()
                self.assertEqual(self.owned(self.opening), [])
                self.assertEqual(self.owned(trigger), [])

    def test_contradictory_terminal_results_never_pass_completion_gate(self):
        for state, recorded in (("success", "action_required"), ("action_required", "success")):
            with self.subTest(state=state, recorded=recorded):
                self.reset()
                owner = self.start()
                check.best_effort_status(HEAD, state, completed_at=DONE)
                self.api.status["body"] = self.api.status["body"].replace(
                    check.STATUS_REVIEWED_RESULT_PREFIX + state + " -->",
                    check.STATUS_REVIEWED_RESULT_PREFIX + recorded + " -->")
                self.assertFalse(check.completed_review_for_owner(self.api.status, owner))
                self.move("head")
                self.refresh()
                self.assertEqual(self.owned(self.opening), [])

    def test_stale_refresh_never_recreates_removed_clean_reaction(self):
        for adverse in (False, True):
            with self.subTest(adverse=adverse):
                trigger = self.reset("issue_comment")
                self.start()
                check.best_effort_status(HEAD, "success", completed_at=DONE)
                if adverse:
                    self.api.faults[("PATCH", f"repos/{REPO}/issues/comments/55")] = "fail"
                    check.best_effort_status(HEAD, "failure")
                    self.api.faults.clear()
                else:
                    self.api.reactions[self.opening] = []
                    self.api.reactions[trigger] = []
                self.assertEqual(self.owned(self.opening) + self.owned(trigger), [])
                self.move("head")
                for _ in range(2):
                    self.refresh()
                    self.assertEqual(self.owned(self.opening) + self.owned(trigger), [])

    def test_status_change_before_reaction_write_blocks_old_positive_signal(self):
        self.reset()
        owner = self.start()
        self.move("head")
        changed = False
        def change(method, path):
            nonlocal changed
            if not changed and method == "GET" and path == self.opening:
                changed = True
                self.api.status["body"] = check.status_comment_body(
                    OTHER, "main", "stale", owner=owner, owner_running=False,
                    reason=check.stale_status_reason(check.STATUS_REASONS["incomplete"]))
        self.api.on_call = change
        check.best_effort_status(HEAD, "success", completed_at=DONE)
        self.assertTrue(changed)
        self.assertNotIn("+1", self.owned(self.opening))
        self.assertFalse(any(method == "POST" and path == self.opening and body == {"content": "+1"}
                             for method, path, body in self.writes()))

    def test_verified_undated_completion_does_not_invent_time(self):
        self.reset()
        self.start()
        check.best_effort_status(HEAD, "success")
        self.move("head")
        self.refresh()
        self.assertEqual(self.owned(self.opening), ["+1"])
        self.assertNotIn(check.STATUS_REVIEWED_COMPLETED_PREFIX, self.api.status["body"])
