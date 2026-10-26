"""Actual no-helper writers retain existing same-diff finding evidence offline."""
import json
import unittest

import test_claude_review_workflows as workflows
import test_claude_review_check as fixtures


class EmergencyFindingRetentionTests(unittest.TestCase):
    def run_fallback(self, path, run=None, response=None, **overrides):
        if response is None:
            response = {"stdout": json.dumps(run)}
        return workflows.ToolingFallbackScriptTests().run_fallback(
            path, {"CHECK": [response]}, PR_NUMBER="7", STATUS_COMMENT_ID="", **overrides)

    def record(self, *, conclusion="failure", evidence=None, **changes):
        evidence = evidence if evidence is not None else {
            "pr_number": 7, "merge_base_sha": fixtures.MERGE_BASE,
            "reviewed_sha": fixtures.HEAD,
            "new_finding_comment_ids": [12], "claude_finding_comment_ids": [12],
            "same_diff_findings_recorded": True,
            "claude_finding_comment_ids_summary": {"count": 200, "truncated": True, "sha256": "f" * 64},
        }
        return {**fixtures.check_run(99, conclusion),
                "output": {"text": "```json\n" + json.dumps(evidence) + "\n```"}, **changes}

    def writes(self, calls):
        return [call for call in calls if call["method"] != "GET"]

    def assert_recovery(self, evidence, *, historical, run=5, attempt=1):
        self.assertEqual(evidence["recovery"], {
            "run_id": run, "run_attempt": attempt,
            "check_conclusion": "failure", "reason": "tooling_unavailable",
            "retained_evidence_scope": "historical" if historical else "none",
        })

    def test_populated_prior_execution_is_historical_through_repeated_recovery(self):
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            for findings in (False, True):
                with self.subTest(workflow=path.name, findings=findings):
                    original = {
                        "pr_number": 7, "reviewed_sha": fixtures.HEAD,
                        "merge_base_sha": fixtures.MERGE_BASE,
                        "runtime_repository": "example/review-runtime", "runtime_sha": "e" * 40,
                        "start_check_result": "success", "review_job_result": "success",
                        "action_conclusion": "success", "completion_verified": True,
                        "completion_reason": "verified", "evidence_errors": [],
                        "finding_publication": {"attempt_count": int(findings),
                                                "comment_ids": [12] if findings else []},
                        "new_finding_comment_ids": [12] if findings else [],
                        "claude_finding_comment_ids": [12] if findings else [],
                        "same_diff_findings_recorded": findings,
                        "selection": {"generation": "d" * 64, "requested_model": "opus",
                                      "requested_effort": "high", "execution_attempt": 1,
                                      "models": ["claude-opus-4-6"], "model_evidence": "observed",
                                      "observed_effort": None},
                    }
                    record = self.record(conclusion="action_required" if findings else "success",
                                         evidence=original)
                    output_size = None
                    for attempt in (2, 3, 4):
                        result, calls = self.run_fallback(path, record, GITHUB_RUN_ATTEMPT=str(attempt))
                        self.assertEqual(result.returncode, 1, result.stderr)
                        failed = {**record, **self.writes(calls)[0]["body"]}
                        self.assertEqual(failed["conclusion"], "failure")
                        evidence = fixtures.check.recorded_evidence(failed)
                        self.assert_recovery(evidence, historical=True, attempt=attempt)
                        self.assertEqual({k: v for k, v in evidence.items() if k != "recovery"}, original)
                        self.assertNotIn("recovery", evidence["recovery"])
                        checks, ids, _ = fixtures.check.prior_finding_evidence(
                            [{"check_runs": [failed]}], fixtures.HEAD, 100, 7, fixtures.MERGE_BASE)
                        self.assertEqual((checks, ids), ([99], {12}) if findings else ([], set()))
                        size = len(failed["output"]["text"].encode())
                        self.assertLess(size, 60000)
                        if output_size is not None:
                            self.assertEqual(size, output_size)
                        output_size = size
                        record = failed

    def test_legacy_execution_provenance_remains_unknown(self):
        original = {"pr_number": 7, "merge_base_sha": fixtures.MERGE_BASE}
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            result, calls = self.run_fallback(path, self.record(conclusion="success", evidence=original))
            self.assertEqual(result.returncode, 1)
            evidence = fixtures.evidence_of(self.writes(calls)[0]["body"])
            self.assert_recovery(evidence, historical=True)
            self.assertEqual({k: v for k, v in evidence.items() if k != "recovery"}, original)
            for key in ("completion_verified", "completion_reason", "selection", "runtime_sha"):
                self.assertNotIn(key, evidence)

    def test_recovery_identity_is_validated_without_backfilling_old_execution(self):
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            for value, expected in (("", None), ("bad", None), ("0", None),
                                    ("1.5", None), ("9007199254740992", None),
                                    ("9007199254740991", 9007199254740991)):
                with self.subTest(workflow=path.name, value=value):
                    result, calls = self.run_fallback(path, self.record(),
                        GITHUB_RUN_ID=value, GITHUB_RUN_ATTEMPT=value)
                    self.assertEqual(result.returncode, 1)
                    self.assert_recovery(fixtures.evidence_of(self.writes(calls)[0]["body"]),
                                         historical=True, run=expected, attempt=expected)

    def test_near_limit_evidence_stays_bounded_and_overflow_leaves_check_unchanged(self):
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            for size, writes_expected in ((58000, True), (59950, False), (60100, False)):
                with self.subTest(workflow=path.name, size=size):
                    evidence = fixtures.check.recorded_evidence(self.record())
                    evidence["legacy_diagnostic"] = ""
                    record = self.record(evidence=evidence)
                    padding = size - len(record["output"]["text"].encode())
                    evidence["legacy_diagnostic"] = "x" * padding
                    result, calls = self.run_fallback(path, self.record(evidence=evidence))
                    self.assertEqual(result.returncode, 1)
                    if writes_expected:
                        text = self.writes(calls)[0]["body"]["output"]["text"]
                        self.assertLess(len(text.encode()), 60000)
                    else:
                        self.assertEqual(self.writes(calls), [])
                        self.assertIn("no Check was changed", result.stdout)

    def test_completed_findings_survive_failure_and_repeat_emergency_recovery(self):
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            for conclusion in ("action_required", "failure"):
                with self.subTest(workflow=path.name, conclusion=conclusion):
                    old = self.record(conclusion=conclusion)
                    result, calls = self.run_fallback(path, old)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(calls[0]["method"], "GET")
                    self.assertIn("/check-runs/99", calls[0]["args"][-1])
                    writes = self.writes(calls)
                    self.assertEqual(len(writes), 1)
                    failed = {**old, **writes[0]["body"]}
                    self.assertEqual(failed["conclusion"], "failure")
                    evidence = fixtures.check.recorded_evidence(failed)
                    self.assert_recovery(evidence, historical=True)
                    self.assertEqual({k: v for k, v in evidence.items() if k != "recovery"},
                                     fixtures.check.recorded_evidence(old))
                    again, repeated = self.run_fallback(path, failed)
                    self.assertEqual(again.returncode, 1)
                    retained = {**failed, **self.writes(repeated)[0]["body"]}
                    checks, ids, _ = fixtures.check.prior_finding_evidence(
                        [{"check_runs": [retained]}], fixtures.HEAD, 100, 7, fixtures.MERGE_BASE)
                    self.assertEqual((checks, ids), ([99], {12}))

    def test_legacy_conclusion_only_presence_gets_durable_flag_before_failure(self):
        evidence = {"pr_number": 7, "merge_base_sha": fixtures.MERGE_BASE}
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(path, self.record(conclusion="action_required", evidence=evidence))
                self.assertEqual(result.returncode, 1)
                retained = fixtures.evidence_of(self.writes(calls)[0]["body"])
                self.assertIs(retained["same_diff_findings_recorded"], True)
                self.assertNotIn("claude_finding_comment_ids", retained)
                self.assert_recovery(retained, historical=True)

    def test_fresh_in_progress_check_needs_no_historical_finding_evidence(self):
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            with self.subTest(workflow=path.name):
                run = self.record(conclusion=None, status="in_progress", output={})
                result, calls = self.run_fallback(path, run)
                self.assertEqual(result.returncode, 1)
                failed = self.writes(calls)[0]["body"]
                self.assertEqual(failed["conclusion"], "failure")
                self.assertEqual(fixtures.evidence_of(failed)["merge_base_sha"], fixtures.MERGE_BASE)
                self.assert_recovery(fixtures.evidence_of(failed), historical=False)

    def test_unavailable_retention_read_is_bounded_and_does_not_write(self):
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(path, response={"hang": True})
                self.assertEqual(result.returncode, 1)
                self.assertEqual([call["method"] for call in calls], ["GET"])
                self.assertIn("no Check was changed", result.stdout)

    def test_unknown_or_mismatched_evidence_never_overwrites_check(self):
        evidence = {"pr_number": 7, "merge_base_sha": fixtures.MERGE_BASE}
        valid = self.record(conclusion="action_required", evidence=evidence)
        invalid = [
            {"fail": True}, {"stdout": "{}"}, {"stdout": "[]"},
            {"stdout": json.dumps({**valid, "id": 100})},
            {"stdout": json.dumps({**valid, "name": "Other Check"})},
            {"stdout": json.dumps({**valid, "app": {"slug": "other-app"}})},
            {"stdout": json.dumps({**valid, "head_sha": "b" * 40})},
            # Legacy conclusion alone is durable evidence. Unknown attribution
            # cannot be erased by changing action_required to failure.
            {"stdout": json.dumps({**valid, "output": {"text": "legacy unknown finding evidence"}})},
            {"stdout": json.dumps({**valid, "output": {"text": "```json\n{\"pr_number\":7,\"pr_number\":7}\n```"}})},
        ]
        for changes in ({"pr_number": 8}, {"merge_base_sha": "b" * 40},
                        {"reviewed_sha": "b" * 40}, {"new_finding_comment_ids": [True]},
                        {"claude_finding_comment_ids": [12, 12]}, {"same_diff_findings_recorded": "true"}):
            invalid.append({"stdout": json.dumps(self.record(evidence={**evidence, **changes}))})
        for path in (workflows.AUTOMATIC, workflows.MANUAL):
            for response in invalid:
                with self.subTest(workflow=path.name, response=response):
                    result, calls = self.run_fallback(path, response=response)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(self.writes(calls), [])
                    self.assertIn("no Check was changed", result.stdout)


if __name__ == "__main__":
    unittest.main()
