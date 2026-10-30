"""Bounded failure diagnostics, actual workflow wiring, and failed publication."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_claude_review_check as fixtures
import test_claude_review_workflows as workflows
import test_presentation_ownership as ownership

check = fixtures.check


def stream(subtype="success", count=31, error=False):
    return [
        {"type": "system", "subtype": "init", "session_id": "synthetic"},
        {"type": "assistant", "session_id": "synthetic", "message": {
            "content": [{"type": "text", "text": "private model text"}]}},
        {"type": "result", "session_id": "synthetic", "subtype": subtype,
         "is_error": error, "num_turns": count},
    ]


def receipt(owner=None, reason="over_limit_success", count=31, maximum=30, execution=None):
    owner = owner or fixtures.presentation_owner()
    return {"version": 1, "generation": owner["generation"], "run": owner["run"],
            "attempt": owner["attempt"], "reason": reason, "num_turns": count, "max_turns": maximum,
            "execution": execution or ("unknown" if reason in ("unavailable", "malformed") else "observed")}


class ExecutionDiagnosticTests(unittest.TestCase):
    def diagnose(self, messages=None, raw=None, maximum=30):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution.json"
            path.write_text(raw if raw is not None else json.dumps(messages or stream()))
            return check.execution_diagnostic(fixtures.presentation_owner(), str(path), maximum)

    def test_observed_over_cap_success_and_explicit_limit_are_distinct(self):
        self.assertEqual(self.diagnose(), receipt())
        self.assertEqual(self.diagnose(stream("error_max_turns", 30, True)),
                         receipt(reason="error_max_turns", count=30))
        # Automatic reviews gain no turn cap from diagnostics.
        self.assertEqual(self.diagnose(maximum=None),
                         receipt(reason="execution_observed", count=None, maximum=None))
        self.assertEqual(self.diagnose(stream(count=30)), receipt(reason="execution_observed", count=None))

    def test_counts_are_strict_integers_and_ambiguous_streams_stay_unknown(self):
        bad = [stream(count=value) for value in (True, False, 31.0, "31", -1, 1_000_001)]
        bad += [stream() + [stream()[-1]], stream() + [{"type": "assistant"}], stream()[1:]]
        wrong_session = stream()
        wrong_session[-1]["session_id"] = "other"
        bad += [wrong_session, [None], {"type": "result"}]
        for messages in bad:
            with self.subTest(messages=messages):
                self.assertEqual(self.diagnose(messages)["reason"], "malformed")
        for raw in ('[{"type":"result","type":"assistant"}]', '{invalid', '[NaN]', '[' * 2000):
            self.assertEqual(self.diagnose(raw=raw)["reason"], "malformed")

    def test_bad_terminal_counts_do_not_erase_valid_execution(self):
        record = self.diagnose(stream(count=True))
        self.assertEqual(record, receipt(reason="malformed", count=None, execution="observed"))

    def test_file_and_message_bounds_and_nonregular_files(self):
        with mock.patch.object(check, "EXECUTION_DIAGNOSTIC_BYTES", 20):
            self.assertEqual(self.diagnose()["reason"], "malformed")
        with mock.patch.object(check, "EXECUTION_DIAGNOSTIC_MESSAGES", 2):
            self.assertEqual(self.diagnose()["reason"], "malformed")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fifo"
            os.mkfifo(path)
            self.assertEqual(check.execution_diagnostic(fixtures.presentation_owner(), str(path))["reason"], "malformed")
            path.unlink()
            path.symlink_to(Path(temporary) / "absent")
            self.assertEqual(check.execution_diagnostic(fixtures.presentation_owner(), str(path))["reason"], "unavailable")
        self.assertEqual(check.execution_diagnostic(fixtures.presentation_owner(), "")["reason"], "unavailable")

    def test_receipt_schema_rejects_wrong_attempt_and_noninteger_counts(self):
        owner = fixtures.presentation_owner()
        for key, value in (("num_turns", True), ("max_turns", False), ("run", True),
                           ("attempt", 2), ("generation", "0" * 64), ("version", True),
                           ("reason", "private error"), ("max_turns", 31)):
            self.assertIsNone(check.valid_execution_diagnostic({**receipt(), key: value}, owner))
        self.assertIsNone(check.valid_execution_diagnostic({**receipt(), "transcript": "private"}, owner))

    def test_actual_diagnostic_step_and_cli_never_export_completion_or_text(self):
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            named = workflows.steps(workflows.job(path, "review"))
            diagnostic = named["Diagnose review execution"]
            condition = workflows.scalar(diagnostic, "if", 8)
            self.assertIn("!cancelled()", condition)
            self.assertIn("steps.review.outcome == 'failure'", condition)
            self.assertIsNone(workflows.scalar(named[workflows.VERIFY_STEP], "if", 8))
            self.assertLess(list(named).index(workflows.VERIFY_STEP), list(named).index("Diagnose review execution"))
            self.assertEqual(workflows.scalar(diagnostic, "timeout-minutes", 8), "1")
            helper = named["Prepare execution diagnostic"]
            self.assertEqual(workflows.scalar(helper, "uses", 8), "$/actions/review-helper")
            self.assertEqual(workflows.scalar(helper, "continue-on-error", 8), "true")
            with tempfile.TemporaryDirectory() as temporary:
                output, record = Path(temporary) / "output", Path(temporary) / "execution.json"
                record.write_text(json.dumps(stream()))
                env = {"PATH": os.environ["PATH"], "REVIEW_HELPER": str(fixtures.SCRIPT),
                       "EXECUTION_FILE": str(record), "GITHUB_OUTPUT": str(output),
                       "PRESENTATION_OWNER": json.dumps(fixtures.presentation_owner()),
                       "GITHUB_RUN_ID": "5", "GITHUB_RUN_ATTEMPT": "1", "REVIEW_MAX_TURNS": "30"}
                run = subprocess.run(["bash", "-eo", "pipefail", "-c", workflows.scalar(diagnostic, "run", 8)],
                                     env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stderr)
                written = output.read_text()
                key, value = written.strip().split("=", 1)
                self.assertEqual(key, "execution_diagnostic")
                self.assertEqual(json.loads(value), receipt())
                self.assertNotIn("private", written + run.stdout + run.stderr)
                self.assertNotIn("completion_verified", written)
                output.unlink()
                env["GITHUB_RUN_ATTEMPT"] = "2"
                run = subprocess.run([sys.executable, str(fixtures.SCRIPT), "diagnose-execution", "--execution", str(record)],
                                     env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertFalse(output.exists())

    def test_manual_cap_has_one_source_and_automatic_policy_is_unchanged(self):
        manual = workflows.job(workflows.MANUAL, "review")
        automatic = workflows.job(workflows.AUTOMATIC, "review")
        self.assertIn('      REVIEW_MAX_TURNS: "100"', manual)
        self.assertIn("    timeout-minutes: 20", manual)
        self.assertIn("    timeout-minutes: 20", automatic)
        args = workflows.folded(workflows.steps(manual)["Review pull request"], "claude_args", 10)
        self.assertIn("--max-turns ${{ env.REVIEW_MAX_TURNS }}", args)
        self.assertNotIn("--max-turns", workflows.folded(workflows.steps(automatic)["Review pull request"], "claude_args", 10))
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            publish = workflows.steps(workflows.job(path, "publish-status"))["Publish Claude Review result"]
            self.assertEqual(workflows.scalar(publish, "EXECUTION_DIAGNOSTIC", 10),
                             "${{ needs.review.outputs.execution_diagnostic }}")


class DiagnosticPresentationTests(unittest.TestCase):
    setUp = ownership.OwnershipTests.setUp
    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target

    def finalize_failure(self, owner, **changes):
        os.environ.update(REVIEW_RESULT="failure", ACTION_CONCLUSION="",
                          COMPLETION_VERIFIED="", COMPLETION_REASON="",
                          EXECUTION_DIAGNOSTIC=json.dumps(receipt(owner)))
        os.environ.update(changes)
        check.best_effort_status(fixtures.HEAD, "failure")
        return self.api.status["body"]

    def test_verified_execution_precedes_workflow_hint_and_keeps_failure(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            if kind != "automatic":
                self.target(kind)
            owner = self.start()
            with mock.patch.object(check, "workflow_changed") as changed:
                body = self.finalize_failure(owner)
            changed.assert_not_called()
            self.assertIn("31 turns", body)
            self.assertIn("maximum of 30", body)
            self.assertIn("/actions/runs/5/attempts/1", body)
            self.assertNotIn("likely caused", body)
            self.assertNotIn("manual-clean-completion", body)
            self.assertEqual(check.status_state(self.api.status), "failure")
            self.assertEqual(check.status_execution_diagnostic(self.api.status, owner), receipt(owner))

    def test_stale_refresh_keeps_owner_bound_receipt_but_new_owner_does_not(self):
        owner = self.start()
        self.finalize_failure(owner)
        self.api.pr["head"]["sha"] = fixtures.OTHER
        os.environ.update(COMPLETION_VERIFIED="true", COMPLETION_REASON="verified", EXECUTION_DIAGNOSTIC="invalid")
        check.best_effort_status(fixtures.OTHER, "stale")
        body = self.api.status["body"]
        self.assertIn("31 turns", body)
        self.assertEqual(check.status_execution_diagnostic(self.api.status, owner), receipt(owner))
        check.best_effort_status(fixtures.OTHER, "stale")
        self.assertEqual(self.api.status["body"], body)
        os.environ.update(HEAD_SHA=fixtures.OTHER, GITHUB_RUN_ID="6", PRESENTATION_START="3")
        check.best_effort_status(fixtures.OTHER, "in_progress", acquire=True)
        self.assertNotIn(check.EXECUTION_DIAGNOSTIC_PREFIX, self.api.status["body"])

    def test_unread_cancellation_and_preparation_precedence(self):
        owner = self.start()
        for changes, reason in (
                ({"REVIEW_RESULT": "cancelled"}, "cancelled"),
                ({"START_RESULT": "failure"}, "preparation_failed"),
                ({"REVIEW_RESULT": "success", "ACTION_CONCLUSION": "success",
                  "COMPLETION_VERIFIED": "false", "COMPLETION_REASON": "captured_inputs_not_read"}, "unread_inputs")):
            os.environ.pop("START_RESULT", None)
            body = self.finalize_failure(owner, **changes)
            self.assertIn(check.STATUS_REASONS[reason], body)
            self.assertNotIn(check.EXECUTION_DIAGNOSTIC_PREFIX, body)
            self.assertNotIn("31 turns", body)

    def test_malformed_missing_and_conflicting_diagnostics_cannot_supply_cause(self):
        owner = self.start()
        for raw in ("", "invalid", json.dumps({**receipt(owner), "attempt": 2}),
                    json.dumps(receipt(owner, reason="malformed", count=None)),
                    json.dumps(receipt(owner, reason="unavailable", count=None))):
            with mock.patch.object(check, "workflow_changed", return_value=None):
                body = self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=raw)
            self.assertNotIn("31 turns", body)
            self.assertNotIn(check.EXECUTION_DIAGNOSTIC_PREFIX, body)
        good = self.finalize_failure(owner)
        for bad in (good + "\n" + check.execution_diagnostic_marker(receipt(owner)),
                    good.replace(check.execution_diagnostic_marker(receipt(owner)), "")):
            self.api.status["body"] = bad
            self.api.pr["head"]["sha"] = fixtures.OTHER
            check.best_effort_status(fixtures.OTHER, "stale")
            self.assertNotIn("31 turns", self.api.status["body"])

    def test_bad_terminal_preserves_execution_and_recursive_receipts_fail_closed(self):
        owner = self.start()
        observed = receipt(owner, reason="malformed", count=None, execution="observed")
        with mock.patch.object(check, "workflow_changed") as changed:
            body = self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=json.dumps(observed))
        changed.assert_not_called()
        self.assertIn(check.STATUS_REASONS["execution_failed"], body)
        self.assertNotIn("turn-limit error", body)
        self.assertNotIn("likely caused", body)
        nested = "[" * 1000 + "0" + "]" * 1000
        with mock.patch.object(check, "workflow_changed", return_value=None):
            self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=nested)
        self.assertIsNone(check.current_execution_diagnostic(owner))
        self.api.status["body"] = body.replace(check.execution_diagnostic_marker(observed),
                                              check.EXECUTION_DIAGNOSTIC_PREFIX + nested + " -->")
        self.assertIsNone(check.status_execution_diagnostic(self.api.status, owner))


class DiagnosticPublicationTests(fixtures.ScriptTestCase):
    finalize = fixtures.FinalizeTests.finalize
    published = fixtures.FinalizeTests.published

    def test_failed_action_diagnostic_never_grants_completion_or_erases_findings(self):
        for reason in ("over_limit_success", "error_max_turns", "execution_observed", "malformed"):
            self.log.unlink(missing_ok=True)
            diagnostic = receipt(reason=reason, count=31 if reason in ("over_limit_success", "error_max_turns") else None)
            rules = [fixtures.history_rule(fixtures.current_run_only()),
                     fixtures.comments_rule(fixtures.ok(fixtures.pages([fixtures.comment(11)]))),
                     fixtures.patch_rule(fixtures.ok("{}"))]
            run = self.finalize(rules, review_result="failure", action_conclusion="",
                                COMPLETION_VERIFIED="", COMPLETION_REASON="",
                                EXECUTION_DIAGNOSTIC=json.dumps(diagnostic))
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            body = self.published()
            self.assertEqual(body["conclusion"], "failure")
            evidence = fixtures.evidence_of(body)
            self.assertIs(evidence["completion_verified"], False)
            self.assertEqual(evidence["claude_finding_comment_ids"], [11])
            self.assertEqual(evidence["execution_diagnostic"], diagnostic)
            self.assertEqual(body["output"]["title"], "Review turn limit reached" if reason in (
                "over_limit_success", "error_max_turns") else "Review did not complete")
            self.assertNotIn("private", body["output"]["summary"] + body["output"]["text"])
