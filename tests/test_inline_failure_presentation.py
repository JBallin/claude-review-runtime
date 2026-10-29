"""Offline failure copy, evidence validation, and attempt ownership regressions."""

import copy
import json
import os
import unittest
from unittest import mock

from test_claude_review_check import check, HEAD, OTHER, presentation_owner
import test_presentation_ownership as ownership


class InlineFailureTests(unittest.TestCase):
    setUp = ownership.OwnershipTests.setUp
    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target

    def finalize_failure(self, count=1, **changes):
        os.environ.update(REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                          COMPLETION_VERIFIED="false", COMPLETION_REASON="errored_inline_tool_result",
                          **changes)
        check.best_effort_status(HEAD, "failure", inline_failure={"check_run_id": 99, "posted_count": count})
        return self.api.status["body"]

    def test_partial_zero_and_unknown_counts_link_the_exact_check(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for count in (0, 1, 2, None):
                with self.subTest(kind=kind, count=count):
                    self.api.status = None
                    if kind != "automatic":
                        self.target(kind)
                    else:
                        os.environ.update(TRIGGER_KIND=kind)
                    owner = self.start()
                    with mock.patch.object(check, "workflow_changed") as paths:
                        body = self.finalize_failure(count)
                    paths.assert_not_called()
                    visible = body.split("<details>", 1)[0]
                    self.assertIn(check.inline_failure_explanation(count), visible)
                    self.assertIn("/pull/7/checks?check_run_id=99", visible)
                    self.assertIn(check.INCOMPLETE_RECOVERY, visible)
                    self.assertIn("For a final candidate with no review in progress", visible)
                    self.assertIn("❌ **Incomplete**", visible)
                    self.assertNotIn("manual-clean-completion", body)
                    self.assertEqual(check.status_inline_failure(self.api.status, owner)["posted_count"], count)
                    self.assertFalse(check.status_owner_running(self.api.status))

    def test_stale_refresh_and_repeated_finalize_keep_only_captured_evidence(self):
        owner = self.start()
        first = self.finalize_failure()
        self.assertEqual(self.finalize_failure(), first)
        self.api.pr["head"]["sha"] = OTHER
        # Refresh's unrelated ambient outputs must not change the old attempt.
        os.environ.update(COMPLETION_REASON="verified", COMPLETION_VERIFIED="true", CHECK_RUN_ID="100")
        with mock.patch.object(check, "workflow_changed") as paths:
            check.best_effort_status(OTHER, "stale")
            once = self.api.status["body"]
            check.best_effort_status(OTHER, "stale")
        paths.assert_not_called()
        self.assertEqual(self.api.status["body"], once)
        self.assertEqual(check.status_owner(self.api.status), owner)
        self.assertEqual(check.status_head(self.api.status), OTHER)
        self.assertIn(check.inline_failure_explanation(1), once)
        self.assertIn("/pull/7/checks?check_run_id=99", once)
        self.assertIn(check.commit_link(HEAD), once)
        self.assertIn("not reviewed", once)
        self.assertNotIn("request an approved fresh", once)

    def test_new_owner_and_delayed_old_finalizer_cannot_borrow_diagnostics(self):
        old = self.start()
        self.finalize_failure()
        self.api.pr["head"]["sha"] = OTHER
        # A real new start supplies the current captured head.
        os.environ.update(HEAD_SHA=OTHER, GITHUB_RUN_ID="6", PRESENTATION_START="3")
        check.best_effort_status(OTHER, "in_progress", acquire=True)
        new = check.status_owner(self.api.status)
        self.assertNotEqual(new, old)
        self.assertNotIn(check.INLINE_FAILURE_PREFIX, self.api.status["body"])
        self.assertNotIn(check.STATUS_REASONS["inline_tool_failed"], self.api.status["body"])
        before = copy.deepcopy(self.api.status)
        os.environ.update(PRESENTATION_OWNER=json.dumps(old), HEAD_SHA=HEAD, GITHUB_RUN_ID="5")
        self.finalize_failure()
        self.assertEqual(self.api.status, before)

    def test_interruption_or_conflicting_verdict_does_not_reuse_failure_copy(self):
        for changes in ({"COMPLETION_VERIFIED": "true"}, {"COMPLETION_VERIFIED": ""},
                        {"ACTION_CONCLUSION": "failure"}, {"REVIEW_RESULT": "cancelled"},
                        {"START_RESULT": "failure"}, {"COMPLETION_REASON": "unknown_reason"}):
            with self.subTest(changes=changes):
                self.api.status = None
                os.environ.pop("START_RESULT", None)
                self.start()
                os.environ.update(REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                                  COMPLETION_VERIFIED="false", COMPLETION_REASON="errored_inline_tool_result")
                os.environ.update(changes)
                check.best_effort_status(HEAD, "failure", inline_failure={"check_run_id": 99, "posted_count": 1})
                body = self.api.status["body"]
                self.assertNotIn(check.INLINE_FAILURE_PREFIX, body)
                self.assertNotIn(check.STATUS_REASONS["inline_tool_failed"], body)
                self.assertNotIn("1 finding was posted", body)

    def test_malformed_or_untrusted_receipts_stay_generic_on_refresh(self):
        owner = self.start()
        good = self.finalize_failure()
        record = {"generation": owner["generation"], "check_run_id": 99, "posted_count": 1}
        marker = check.inline_failure_marker(record)
        invalid = []
        for field, value in (("generation", "0" * 64), ("posted_count", True), ("posted_count", -1),
                             ("posted_count", "1"), ("check_run_id", False), ("check_run_id", 0),
                             ("check_run_id", "99")):
            invalid.append(good.replace(marker, check.inline_failure_marker({**record, field: value})))
        invalid.extend([
                        good.replace(check.STATUS_STATE_PREFIX + "failure -->", check.STATUS_STATE_PREFIX + "success -->"),
                        good.replace(check.STATUS_STATE_PREFIX + "failure -->", ""),
                        good + "\n" + check.STATUS_STATE_PREFIX + "success -->",
                        good + "\n" + check.STATUS_STATE_PREFIX + "malformed",
                        good.replace(check.STATUS_STATE_PREFIX + "failure -->", check.STATUS_STATE_PREFIX + "stale -->"),
                        good.replace(marker, ""), good + "\n" + marker,
                        good.replace(marker, check.INLINE_FAILURE_PREFIX + "not-json -->"),
                        good.replace("**Reason:**", "**Reason:** unknown\n**Reason:**"),
                        good.replace("owner-phase:terminal", "owner-phase:running")])
        for body in invalid:
            with self.subTest(body=body[:80]):
                self.api.status["body"] = body
                self.assertIsNone(check.status_inline_failure(self.api.status, owner))
                self.api.pr["head"]["sha"] = OTHER
                check.best_effort_status(OTHER, "stale")
                visible = self.api.status["body"].split("<details>", 1)[0]
                self.assertNotIn("1 finding was posted", visible)
                self.assertNotIn("check_run_id=99", visible)
        self.api.status["body"] = good
        self.api.status["user"] = {"login": "human", "type": "User"}
        self.assertIsNone(check.status_inline_failure(self.api.status, owner))
        self.assertIsNone(check.status_inline_failure({"body": good}, presentation_owner(run=6)))

    def test_head_movement_during_finalization_preserves_attempt_not_fresh_review_hint(self):
        owner = self.start()
        self.api.pr["head"]["sha"] = OTHER
        body = self.finalize_failure(2)
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertEqual(check.status_owner(self.api.status), owner)
        self.assertIn(check.inline_failure_explanation(2), body)
        self.assertNotIn("request an approved fresh", body)


if __name__ == "__main__":
    unittest.main()
