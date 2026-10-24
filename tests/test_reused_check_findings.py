"""Finding evidence survives partial reruns that reuse the start job's Check."""
import json

import test_claude_review_check as fixtures


check = fixtures.check


class ReusedCheckFindingTests(fixtures.ScriptTestCase):
    finalize = fixtures.FinalizeTests.finalize
    published = fixtures.FinalizeTests.published

    def existing(self, *, conclusion="action_required", ids=(12,), presence=True, **changes):
        run = fixtures.check_run(99, conclusion, **changes)
        evidence = {"pr_number": 7, "reviewed_sha": fixtures.HEAD,
                    "merge_base_sha": fixtures.MERGE_BASE,
                    "same_diff_findings_recorded": presence,
                    "claude_finding_comment_ids": list(ids)}
        run["output"] = {"text": check.evidence_text(evidence)}
        return run

    def finish(self, run, **env):
        self.log.unlink(missing_ok=True)
        return self.finalize([
            fixtures.history_rule(fixtures.ok(fixtures.history_pages({"check_runs": [run]}))),
            fixtures.comments_rule(fixtures.ok(fixtures.pages([]))),
            fixtures.patch_rule(fixtures.ok("{}"))], **env)

    def assert_retained(self, body, *, ids=(12,), conclusion="action_required"):
        self.assertEqual(body["conclusion"], conclusion)
        evidence = fixtures.evidence_of(body)
        self.assertIs(evidence["same_diff_findings_recorded"], True)
        self.assertEqual(evidence["claude_finding_comment_ids"], list(ids))

    def test_reused_check_retains_deleted_findings_through_clean_or_failed_verdict(self):
        for previous in ("action_required", "failure"):
            for result in ("success", "failure", "cancelled", "skipped"):
                with self.subTest(previous=previous, result=result):
                    outcome = self.finish(self.existing(conclusion=previous), review_result=result)
                    self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
                    self.assert_retained(self.published(),
                                         conclusion="action_required" if result == "success" else "failure")

    def test_receipt_replay_fails_now_but_preserves_findings_for_later_clean_run(self):
        result = self.finish(self.existing(), FINDING_PUBLICATION=json.dumps({
            "attempt_count": 1, "comment_ids": [12]}))
        self.assertEqual(result.returncode, 1)
        failed = self.published()
        self.assert_retained(failed, conclusion="failure")
        self.assertIn("could not be confirmed", fixtures.evidence_of(failed)["evidence_errors"][0])
        # A genuinely new start creates a different Check. No comment remains;
        # the failed reused Check must still supply its durable finding state.
        prior = {**self.existing(conclusion="failure"), "output": failed["output"]}
        self.log.unlink()
        result = self.finalize([
            fixtures.history_rule(fixtures.ok(fixtures.history_pages({"check_runs": [
                fixtures.check_run(99, None, status="in_progress"), {**prior, "id": 98}]}))),
            fixtures.comments_rule(fixtures.ok(fixtures.pages([]))),
            fixtures.patch_rule(fixtures.ok("{}"))])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_retained(self.published())
        self.assertEqual(fixtures.evidence_of(self.published())["prior_finding_check_run_ids"], [98])

    def test_presence_only_and_legacy_conclusion_only_remain_sticky(self):
        for run in (self.existing(ids=()), fixtures.check_run(99, "action_required")):
            with self.subTest(run=run):
                result = self.finish(run)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_retained(self.published(), ids=())

    def test_other_completed_checks_do_not_change_same_diff_scope(self):
        for changes in ({"pr": 8}, {"merge_base": fixtures.OTHER},
                        {"slug": "other-app"}, {"name": "Different"}):
            with self.subTest(changes=changes):
                prior = fixtures.check_run(98, "action_required", **changes)
                self.log.unlink(missing_ok=True)
                result = self.finalize([
                    fixtures.history_rule(fixtures.ok(fixtures.history_pages({"check_runs": [
                        fixtures.check_run(99, None, status="in_progress"), prior]}))),
                    fixtures.comments_rule(fixtures.ok(fixtures.pages([]))),
                    fixtures.patch_rule(fixtures.ok("{}"))])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.published()["conclusion"], "success")

    def test_history_outage_preserves_directly_read_findings_on_failed_completion(self):
        result = self.finalize([
            fixtures.history_rule(fixtures.FAIL),
            {"method": "GET", "path": f"repos/{fixtures.REPO}/check-runs/99",
             "responses": [fixtures.ok(json.dumps(self.existing()))]},
            fixtures.comments_rule(fixtures.ok(fixtures.pages([]))),
            fixtures.patch_rule(fixtures.ok("{}"))])
        self.assertEqual(result.returncode, 1)
        self.assert_retained(self.published(), conclusion="failure")

    def test_unreadable_or_ambiguous_existing_record_is_never_overwritten(self):
        bad = self.existing()
        bad["output"]["text"] = '```json\n{"pr_number":7,"pr_number":8}\n```'
        wrong = self.existing()
        wrong["output"]["text"] = check.evidence_text({"pr_number": 8,
                                                     "merge_base_sha": fixtures.MERGE_BASE})
        for response in (fixtures.FAIL, fixtures.ok(json.dumps(bad)),
                         fixtures.ok(json.dumps(wrong)),
                         fixtures.ok(json.dumps(self.existing(status="in_progress"))),
                         fixtures.ok(json.dumps(fixtures.check_run(99, "action_required",
                                                                  status="in_progress", legacy=True))),
                         fixtures.ok(json.dumps(fixtures.check_run(99, "action_required", legacy=True)))):
            with self.subTest(response=response):
                self.log.unlink(missing_ok=True)
                result = self.finalize([
                    fixtures.history_rule(fixtures.FAIL),
                    {"method": "GET", "path": f"repos/{fixtures.REPO}/check-runs/99",
                     "responses": [response]},
                    fixtures.comments_rule(fixtures.ok(fixtures.pages([]))),
                    fixtures.patch_rule(fixtures.ok("{}"))])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.calls("PATCH"), [])
                self.assertEqual(self.calls("POST"), [])
                self.assertIn("Check was not changed", result.stdout)

    def test_helper_fallback_failure_keeps_reused_check_presence(self):
        result = self.finalize([
            fixtures.history_rule(fixtures.ok(fixtures.history_pages({"check_runs": [self.existing()]}))),
            fixtures.comments_rule(fixtures.ok(fixtures.pages([]))),
            fixtures.patch_rule(fixtures.FAIL, fixtures.FAIL, fixtures.FAIL, fixtures.ok("{}"))])
        self.assertEqual(result.returncode, 1)
        self.assert_retained(self.published(), conclusion="failure")
