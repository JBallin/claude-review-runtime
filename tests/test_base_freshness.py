"""Synthetic direct-ref freshness regressions; no network or model execution."""
import json
import os
from unittest import mock

from test_claude_review_check import (
    ScriptTestCase, check, SCRIPT, HEAD, OTHER, BASE_TIP, REPO,
    ok, FAIL, live_head, pr_head_rule, base_tip_rule,
)


class ManualAdmissionTests(ScriptTestCase):
    def run_admission(self, rules=None, **extra):
        from test_claude_review_workflows import MANUAL, job, steps, run_block
        script = run_block(steps(job(MANUAL, "start-check"))["Verify manual base freshness"])
        return self.run_script(["admit-manual"], rules or [pr_head_rule(live_head(HEAD))],
                               step_script=script, REVIEW_HELPER=str(SCRIPT), **extra)

    def test_actual_step_accepts_fresh_snapshot_using_only_reads(self):
        result = self.run_admission()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([call["path"] for call in self.calls()], [
            f"repos/{REPO}/pulls/7", f"repos/{REPO}/git/ref/heads/main", f"repos/{REPO}/pulls/7"])
        self.assertEqual(self.calls("POST") + self.calls("PATCH"), [])

    def test_actual_step_rejects_old_projection_before_model_job(self):
        result = self.run_admission([pr_head_rule(live_head(HEAD)), base_tip_rule("main", OTHER)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("comparison base differs", result.stdout)
        self.assertFalse(self.calls("POST") + self.calls("PATCH"))

    def test_head_or_base_retarget_during_lookup_rejects(self):
        for after in (live_head(OTHER), live_head(HEAD, "release"), live_head(HEAD, base_sha=OTHER)):
            with self.subTest(after=after):
                self.log.unlink(missing_ok=True)
                result = self.run_admission([pr_head_rule(live_head(HEAD), after)])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("changed during", result.stdout)

    def test_foreign_base_repository_rejects_before_ref_lookup(self):
        pr = json.loads(live_head(HEAD)["stdout"])
        pr["base"]["repo"]["full_name"] = "other/repository"
        result = self.run_admission([pr_head_rule(ok(json.dumps(pr)))])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls()), 1)

    def test_captured_head_mismatch_rejects_before_ref_lookup(self):
        result = self.run_admission([pr_head_rule(live_head(OTHER))])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls()), 1)

    def test_missing_or_malformed_ref_authority_rejects(self):
        valid = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": BASE_TIP}}
        invalid = [FAIL, ok("[]"), ok("{}"), ok('{"ref":"refs/heads/main","object":null}')]
        for field, value in (("ref", "refs/heads/main/other"), ("type", "tag"),
                             ("sha", 1), ("sha", OTHER.upper()), ("sha", "short")):
            ref = json.loads(json.dumps(valid))
            (ref if field == "ref" else ref["object"])[field] = value
            invalid.append(ok(json.dumps(ref)))
        invalid.append(ok('{"ref":"refs/heads/main","ref":"refs/heads/main",'
                          '"object":{"type":"commit","sha":"' + BASE_TIP + '"}}'))
        for response in invalid:
            with self.subTest(response=response):
                self.log.unlink(missing_ok=True)
                result = self.run_admission([pr_head_rule(live_head(HEAD)),
                                            base_tip_rule("main", BASE_TIP, response)])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertLessEqual(len([c for c in self.calls() if "/git/ref/" in c["path"]]), check.ATTEMPTS)
                self.assertFalse(self.calls("POST") + self.calls("PATCH"))

    def test_slash_branch_reads_only_the_exact_encoded_ref(self):
        result = self.run_admission([pr_head_rule(live_head(HEAD, "release/2026"))], BASE_REF="release/2026")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"repos/{REPO}/git/ref/heads/release%2F2026", [c["path"] for c in self.calls()])

    def test_admission_budget_is_cleared_before_authoritative_publication(self):
        with mock.patch.dict(os.environ, {"REPO": REPO, "PR_NUMBER": "7", "HEAD_SHA": HEAD,
                                          "BASE_REF": "main", "BASE_SHA": BASE_TIP}):
            with mock.patch.object(check, "live_pr_projection", side_effect=ValueError("invalid authority")):
                self.assertEqual(check.cmd_admit_manual(), 1)
        self.assertIsNone(check.PRESENTATION_DEADLINE)

    def test_missing_admission_tooling_is_not_bypassed_for_forks(self):
        from test_claude_review_workflows import MANUAL, job, steps, run_block
        script = run_block(steps(job(MANUAL, "start-check"))["Verify manual base freshness"])
        result = self.run_script(["admit-manual"], [], step_script=script, REVIEW_HELPER="", IS_FORK="true")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])
