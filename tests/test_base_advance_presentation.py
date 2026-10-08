"""Offline completed-result presentation when only the target baseline advances."""
import copy
import os
import unittest

from test_claude_review_check import check, HEAD, OTHER
import test_presentation_ownership as ownership

DONE = "2026-10-07T13:09:19Z"


class BaseAdvancePresentationTests(unittest.TestCase):
    def setUp(self):
        ownership.OwnershipTests.setUp(self)
        os.environ["DETAILS_URL"] = "https://github.com/owner/repo/actions/runs/5"
    start = ownership.OwnershipTests.start

    def finish(self, result="success", when=DONE, available=True):
        check.best_effort_status(HEAD, result, completed_at=when, check_available=available)

    def assert_completed_result(self, result):
        body = self.api.status["body"]
        label = "reviewed clean" if result == "success" else "reviewed with findings"
        self.assertIn("## Claude Review", body)
        self.assertNotIn("**Last completed review**", body)
        self.assertIn("✅ **Completed**" if result == "success" else "⚠️ **Findings**", body)
        self.assertIn(f"**Current commit:** `{HEAD[:7]}` on `main` — {label}", body)
        self.assertIn("**Current baseline:** " + f"`{self.api.base_tip[:7]}` — integration not reviewed", body)
        self.assertIn(check.STATUS_REASONS["base_advanced"], body)
        self.assertNotIn("Request a new review", body)
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertFalse(check.status_owner_running(self.api.status))
        self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", result))
        if result == "action_required":
            self.assertNotIn("reviewed clean", body)
            self.assertNotIn("Claude Review passed", body)

    def assert_no_positive_result(self):
        body = self.api.status["body"]
        self.assertIn("Claude Review", body)
        if "| ✅ **Completed**" in body:
            self.assertIn("**Last completed review**", body)
        self.assertNotIn(" — base advanced", body)
        self.assertIn(" — not reviewed", body)

    def test_completed_result_survives_base_movement_during_or_after_review(self):
        for result in ("success", "action_required"):
            for during in (True, False):
                with self.subTest(result=result, during=during):
                    self.api.status = None
                    self.api.base_tip = os.environ["BASE_SHA"]
                    owner = self.start()
                    if during:
                        self.api.base_tip = OTHER
                    self.finish(result)
                    if not during:
                        self.api.base_tip = OTHER
                        check.best_effort_status(HEAD, "stale")
                    self.assert_completed_result(result)
                    details = check.last_completed_review_details(self.api.status)
                    self.assertEqual(details["completed_at"], DONE)
                    self.assertEqual(details["generation"], owner["generation"])
                    self.assertEqual(details["base_sha"], owner["base"])
                    for _ in range(2):
                        check.best_effort_status(HEAD, "stale")
                        self.assert_completed_result(result)
                        self.assertEqual(check.last_completed_review_details(self.api.status), details)
                    self.assertEqual([item["content"] for item in self.api.reactions.get(self.opening, [])],
                                     ["+1"] if result == "success" else [])

    def test_changed_head_or_target_ref_remains_stale(self):
        for changed_head in (True, False):
            with self.subTest(changed_head=changed_head):
                self.api.status = None
                self.api.pr["head"]["sha"], self.api.pr["base"]["ref"] = HEAD, "main"
                os.environ["BASE_REF"] = "main"
                self.start()
                self.finish()
                self.api.base_tip = OTHER
                if changed_head:
                    self.api.pr["head"]["sha"] = OTHER
                else:
                    self.api.pr["base"]["ref"] = "release"
                    os.environ["BASE_REF"] = "release"
                check.best_effort_status(self.api.pr["head"]["sha"], "stale")
                body = self.api.status["body"]
                self.assertIn("## Claude Review", body)
                self.assertIn("⚠️ This completed review doesn’t cover the current version.", body)
                self.assertNotIn(" — base advanced", body)
                self.assertIn(" — not reviewed", body)
                self.assertEqual(check.status_state(self.api.status), "stale")

    def test_running_newer_failed_and_same_owner_failed_attempts_remain_stale(self):
        for result in ("in_progress", "failure", "cancelled", "publication_incomplete", "same_owner_failure"):
            with self.subTest(result=result):
                self.api.status = None
                self.api.base_tip = os.environ["BASE_SHA"]
                os.environ.update(GITHUB_RUN_ID="5", PRESENTATION_START="2", REVIEW_RESULT="success")
                self.start()
                self.finish()
                if result != "same_owner_failure":
                    self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3)
                if result not in ("in_progress",):
                    if result == "cancelled":
                        os.environ["REVIEW_RESULT"] = "cancelled"
                    self.finish("publication_incomplete" if result == "publication_incomplete" else "failure")
                self.api.base_tip = OTHER
                check.best_effort_status(HEAD, "stale")
                self.assert_no_positive_result()

    def test_undated_legacy_completion_requires_existing_trusted_migration(self):
        self.start()
        self.finish(when=None)
        self.api.status["body"] = "\n".join(line for line in self.api.status["body"].splitlines()
            if not line.startswith(check.STATUS_REVIEWED_GENERATION_PREFIX))
        self.api.base_tip = OTHER
        check.best_effort_status(HEAD, "stale")
        self.assert_completed_result("success")
        self.assertNotIn("completed_at", check.last_completed_review_details(self.api.status))
        # Already-stale legacy history cannot prove which attempt completed.
        self.api.status["body"] = "\n".join(line for line in self.api.status["body"].splitlines()
            if not line.startswith(check.STATUS_REVIEWED_GENERATION_PREFIX))
        check.best_effort_status(HEAD, "stale")
        self.assert_no_positive_result()

    def test_ambiguous_completion_metadata_cannot_gain_positive_presentation(self):
        self.start()
        self.finish()
        original = copy.deepcopy(self.api.status)
        owner = check.status_owner(original)
        for prefix, value in (
                (check.STATUS_REVIEWED_HEAD_PREFIX, OTHER),
                (check.STATUS_REVIEWED_BASE_REF_PREFIX, "release"),
                (check.STATUS_REVIEWED_RESULT_PREFIX, "action_required"),
                (check.STATUS_REVIEWED_BASE_SHA_PREFIX, OTHER),
                (check.STATUS_REVIEWED_GENERATION_PREFIX, "f" * 64),
                (check.STATUS_REVIEWED_COMPLETED_PREFIX, DONE),
                (check.STATUS_REVIEWED_RUN_URL_PREFIX, "https%3A%2F%2Fexample.com")):
            with self.subTest(prefix=prefix):
                self.api.status = copy.deepcopy(original)
                self.api.status["body"] += f"\n{prefix}{value} -->"
                self.api.base_tip = OTHER
                check.best_effort_status(HEAD, "stale")
                self.assert_no_positive_result()
                self.assertEqual(check.status_owner(self.api.status), owner)
                check.best_effort_status(HEAD, "stale")
                self.assert_no_positive_result()

    def test_unavailable_check_and_restored_baseline_do_not_gain_base_advanced_claim(self):
        self.start()
        self.api.base_tip = OTHER
        self.finish(available=False)
        self.assert_no_positive_result()
        self.api.status = None
        self.api.base_tip = os.environ["BASE_SHA"]
        self.start()
        self.finish()
        self.api.base_tip = OTHER
        check.best_effort_status(HEAD, "stale")
        self.assert_completed_result("success")
        self.api.base_tip = os.environ["BASE_SHA"]
        check.best_effort_status(HEAD, "stale")
        self.assert_no_positive_result()
        self.assertEqual(check.last_completed_review_details(self.api.status)["completed_at"], DONE)
