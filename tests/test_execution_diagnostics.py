"""Bounded failure diagnostics, actual workflow wiring, and failed publication."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys
import textwrap
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
    return {"version": 2, "generation": owner["generation"], "run": owner["run"],
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
                         receipt(reason="execution_observed", count=31, maximum=None))
        self.assertEqual(self.diagnose(stream(count=30)), receipt(reason="execution_observed", count=30))

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

    def test_uncapped_counts_and_historical_receipts(self):
        owner = fixtures.presentation_owner()
        for subtype, error in (("success", False), ("error_max_turns", True), ("error_during_execution", True)):
            for count in (0, 33, None):
                record = self.diagnose(stream(subtype, count, error), maximum=None)
                self.assertEqual(record["num_turns"], count)
                self.assertIsNone(record["max_turns"])
                self.assertEqual(check.valid_execution_diagnostic(record, owner), record)
        old = {**receipt(), "version": 1}
        self.assertEqual(check.valid_execution_diagnostic(old, owner), old)
        old.update(reason="execution_observed", num_turns=None)
        self.assertEqual(check.turns_line(check.valid_execution_diagnostic(old, owner)), "**Turns:** unavailable")
        for key, value in (("is_error", "false"), ("subtype", "model prose")):
            messages = stream()
            messages[-1][key] = value
            self.assertIsNone(self.diagnose(messages, maximum=None)["num_turns"])

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
                summary = Path(temporary) / "summary"
                record.write_text(json.dumps(stream()))
                env = {"PATH": os.environ["PATH"], "REVIEW_HELPER": str(fixtures.SCRIPT),
                       "EXECUTION_FILE": str(record), "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary),
                       "PRESENTATION_OWNER": json.dumps(fixtures.presentation_owner()),
                       "GITHUB_RUN_ID": "5", "GITHUB_RUN_ATTEMPT": "1", "REVIEW_MAX_TURNS": "30"}
                run = subprocess.run(["bash", "-eo", "pipefail", "-c", textwrap.dedent("\n".join(diagnostic[diagnostic.index("        run: |") + 1:]))],
                                     env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stderr)
                written = output.read_text()
                key, value = written.strip().split("=", 1)
                self.assertEqual(key, "execution_diagnostic")
                self.assertEqual(json.loads(value), receipt())
                self.assertEqual(summary.read_text(), check.execution_summary(json.loads(value), fixtures.presentation_owner()))
                self.assertNotIn("private", written + run.stdout + run.stderr)
                self.assertNotIn("completion_verified", written)
                output.unlink()
                summary.unlink()
                env.pop("REVIEW_MAX_TURNS")
                run = subprocess.run(["bash", "-eo", "pipefail", "-c", textwrap.dedent(
                    "\n".join(diagnostic[diagnostic.index("        run: |") + 1:]))],
                    env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stderr)
                uncapped = json.loads(output.read_text().strip().split("=", 1)[1])
                self.assertEqual(uncapped, receipt(reason="execution_observed", count=31, maximum=None))
                self.assertEqual(summary.read_text(), check.execution_summary(uncapped, fixtures.presentation_owner()))
                output.unlink()
                summary.unlink()
                env["REVIEW_HELPER"] = ""
                run = subprocess.run(["bash", "-eo", "pipefail", "-c", textwrap.dedent(
                    "\n".join(diagnostic[diagnostic.index("        run: |") + 1:]))],
                    env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(summary.read_text(), "**Turns:** unavailable\n")
                self.assertFalse(output.exists())
                env["GITHUB_RUN_ATTEMPT"] = "2"
                run = subprocess.run([sys.executable, str(fixtures.SCRIPT), "diagnose-execution", "--execution", str(record)],
                                     env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertFalse(output.exists())

    def test_both_review_jobs_are_uncapped_with_20_minute_timeout(self):
        manual = workflows.job(workflows.MANUAL, "review")
        automatic = workflows.job(workflows.AUTOMATIC, "review")
        self.assertNotIn("REVIEW_MAX_TURNS", "\n".join(manual))
        self.assertIn("    timeout-minutes: 20", manual)
        self.assertIn("    timeout-minutes: 20", automatic)
        args = workflows.folded(workflows.steps(manual)["Review pull request"], "claude_args", 10)
        self.assertNotIn("--max-turns", args)
        self.assertNotIn("--max-turns", workflows.folded(workflows.steps(automatic)["Review pull request"], "claude_args", 10))
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            publish = workflows.steps(workflows.job(path, "publish-status"))["Publish Claude Review result"]
            self.assertEqual(workflows.scalar(publish, "EXECUTION_DIAGNOSTIC", 10),
                             "${{ needs.review.outputs.execution_diagnostic }}")



class EmergencyExecutionRetentionTests(unittest.TestCase):
    def setUp(self):
        path = os.environ["PATH"]
        ownership.OwnershipTests.setUp(self)
        os.environ["PATH"] = path

    def scenario(self, workflow):
        self.api.pr["head"]["sha"] = fixtures.HEAD
        prior = fixtures.presentation_owner()
        current = fixtures.presentation_owner(run=6, started=3,
            kind="automatic" if workflow == workflows.AUTOMATIC else "issue_comment",
            target=None if workflow == workflows.AUTOMATIC else 12)
        record = receipt(prior, reason="execution_observed", count=33, maximum=None)
        details = {"base_sha": prior["base"], "generation": prior["generation"],
                   "run_url": f"https://github.com/{prior['repo']}/actions/runs/{prior['run']}",
                   "execution": {"owner": prior, "record": record}}
        body = check.status_comment_body(fixtures.HEAD, "main", "in_progress", owner=current,
            last_review=(fixtures.HEAD, "main", "success"), last_review_details=details)
        return prior, current, record, body

    def repair(self, workflow, owner, body):
        owned = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"}, "body": body}
        run, calls = workflows.ToolingFallbackScriptTests().run_fallback(workflow,
            OWNER_FIXTURE=owner, REPO=owner["repo"], PR_NUMBER="7", STATUS_COMMENT_ID="55",
            GITHUB_RUN_ID=str(owner["run"]), GITHUB_RUN_ATTEMPT=str(owner["attempt"]),
            responses={"STATUS": [{"stdout": json.dumps(owned)}],
                       "PR": [{"stdout": json.dumps(self.api.pr)}]})
        self.assertEqual(run.returncode, 1, run.stderr)
        repaired = [c["body"]["body"] for c in calls if c["method"] == "PATCH"
                    and any("issues/comments/55" in a for a in c["args"])]
        self.assertTrue(repaired, run.stdout + run.stderr + str(calls))
        return repaired[-1]

    def test_emergency_rewrite_preserves_prior_receipt_for_normal_refresh(self):
        for workflow in (workflows.AUTOMATIC, workflows.MANUAL):
            with self.subTest(workflow=workflow.name):
                prior, current, record, body = self.scenario(workflow)
                repaired = self.repair(workflow, current, body)
                self.api.status = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                                   "body": repaired}
                self.assertEqual(check.last_completed_review_details(self.api.status)["execution"],
                                 {"owner": prior, "record": record})
                self.assertIsNone(check.status_execution_record(self.api.status, current))
                os.environ.update(PRESENTATION_OWNER=json.dumps(current), GITHUB_RUN_ID="6",
                    PRESENTATION_START="3", DETAILS_URL=f"https://github.com/{current['repo']}/actions/runs/6")
                self.api.pr["head"]["sha"] = fixtures.OTHER
                check.best_effort_status(fixtures.OTHER, "stale")
                self.assertEqual(check.last_completed_review_details(self.api.status)["execution"]["record"], record)
                self.assertIn("**Turns:** 33", self.api.status["body"])
                self.assertIn("**Turns:** unavailable", self.api.status["body"])

    def test_emergency_rejects_untrusted_or_ambiguous_historical_receipts(self):
        from urllib.parse import quote
        for workflow in (workflows.AUTOMATIC, workflows.MANUAL):
            prior, current, record, body = self.scenario(workflow)
            original = re.search(re.escape(check.REVIEWED_EXECUTION_PREFIX) + r".*? -->", body)[0]
            def marker(value):
                return check.REVIEWED_EXECUTION_PREFIX + quote(json.dumps(value, sort_keys=True,
                    separators=(",", ":")), safe="") + " -->"
            wrong = fixtures.presentation_owner(repo="other/repo")
            wrong_run = fixtures.presentation_owner(run=9)
            wrong_pr = fixtures.presentation_owner(pr=8)
            candidates = [
                "broken", "x" * 4097,
                marker({"owner": wrong, "record": receipt(wrong)}),
                marker({"owner": wrong_pr, "record": receipt(wrong_pr)}),
                marker({"owner": wrong_run, "record": receipt(wrong_run)}),
                marker({"owner": prior, "record": {**record, "generation": current["generation"]}}),
                marker({"owner": prior, "record": {**record, "num_turns": True}}),
                marker({"owner": {**prior, "generation": "0" * 64}, "record": record}),
                original + "\n" + original,
                check.REVIEWED_EXECUTION_PREFIX + quote('{"owner":{},"owner":{},"record":{}}', safe="") + " -->",
            ]
            for candidate in candidates:
                with self.subTest(workflow=workflow.name, candidate=candidate[:80]):
                    replacement = candidate if candidate.startswith(check.REVIEWED_EXECUTION_PREFIX) else check.REVIEWED_EXECUTION_PREFIX + candidate + " -->"
                    repaired = self.repair(workflow, current, body.replace(original, replacement))
                    self.assertNotIn(check.REVIEWED_EXECUTION_PREFIX, repaired)
                    comment = {"user": {"login": check.STATUS_AUTHOR, "type": "Bot"}, "body": repaired}
                    self.assertNotIn("execution", check.last_completed_review_details(comment))
                    self.assertEqual(check.last_completed_review(comment), (fixtures.HEAD, "main", "success"))


class DiagnosticPresentationTests(unittest.TestCase):
    setUp = ownership.OwnershipTests.setUp
    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target

    def test_count_is_informational_and_shared_with_summary_through_stale_refresh(self):
        owner = self.start()
        record = receipt(owner, reason="execution_observed", count=33, maximum=None)
        os.environ.update(REVIEW_RESULT="success", ACTION_CONCLUSION="success", COMPLETION_VERIFIED="false",
                          COMPLETION_REASON="captured_inputs_not_read", EXECUTION_DIAGNOSTIC=json.dumps(record))
        check.best_effort_status(fixtures.HEAD, "failure")
        self.assertEqual(check.status_state(self.api.status), "failure")
        self.assertIsNone(check.last_completed_review(self.api.status))
        self.assertIn(check.STATUS_REASONS["unread_inputs"], self.api.status["body"])
        self.assertIn(check.turns_line(record), self.api.status["body"])
        self.assertIn(check.turns_line(record), check.execution_summary(record, owner))
        self.api.pr["head"]["sha"] = fixtures.OTHER
        check.best_effort_status(fixtures.OTHER, "stale")
        self.assertEqual(check.status_execution_record(self.api.status, owner), record)
        self.assertIn("**Turns:** 33", self.api.status["body"])
        # Exact run/attempt mismatches cannot supply even informational counts.
        for key, value in (("GITHUB_RUN_ID", "6"), ("GITHUB_RUN_ATTEMPT", "2")):
            with mock.patch.dict(os.environ, {key: value}):
                self.assertIsNone(check.current_execution_record(owner))

    def test_completed_count_stays_with_its_history_and_new_attempt_has_own_count(self):
        owner = self.start()
        record = receipt(owner, reason="execution_observed", count=33, maximum=None)
        os.environ.update(REVIEW_RESULT="success", ACTION_CONCLUSION="success", COMPLETION_VERIFIED="true",
                          COMPLETION_REASON="verified", EXECUTION_DIAGNOSTIC=json.dumps(record),
                          DETAILS_URL=f"https://github.com/{fixtures.REPO}/actions/runs/5")
        check.best_effort_status(fixtures.HEAD, "success")
        self.assertEqual(check.last_completed_review_details(self.api.status)["execution"]["record"], record)
        os.environ["EXECUTION_DIAGNOSTIC"] = ""
        check.best_effort_status(fixtures.HEAD, "success")
        self.assertEqual(check.last_completed_review_details(self.api.status)["execution"]["record"], record)
        self.api.pr["head"]["sha"] = fixtures.OTHER
        check.best_effort_status(fixtures.OTHER, "stale")
        self.assertIn("**Turns:** 33", self.api.status["body"])
        os.environ.update(HEAD_SHA=fixtures.OTHER, GITHUB_RUN_ID="6", PRESENTATION_START="3")
        check.best_effort_status(fixtures.OTHER, "in_progress", acquire=True)
        newer = check.status_owner(self.api.status)
        os.environ["PRESENTATION_OWNER"] = json.dumps(newer)
        current, previous = self.api.status["body"].split("**Previous completed review**", 1)
        self.assertIn("**Turns:** unavailable", current)
        self.assertIn("**Turns:** 33", previous)
        second = receipt(newer, reason="execution_observed", count=7, maximum=None)
        os.environ.update(REVIEW_RESULT="failure", ACTION_CONCLUSION="", COMPLETION_VERIFIED="",
                          COMPLETION_REASON="", EXECUTION_DIAGNOSTIC=json.dumps(second))
        check.best_effort_status(fixtures.OTHER, "failure")
        current, previous = self.api.status["body"].split("**Previous completed review**", 1)
        self.assertIn("**Turns:** 7", current)
        self.assertIn("**Turns:** 33", previous)
        self.assertEqual(check.last_completed_review(self.api.status), (fixtures.HEAD, "main", "success"))

    def test_conflicting_same_attempt_counts_stay_unavailable(self):
        owner = self.start()
        original = receipt(owner, reason="execution_observed", count=33, maximum=None)
        self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=json.dumps(original))
        conflicting = {**original, "num_turns": 7}
        body = self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=json.dumps(conflicting))
        self.assertIn("**Turns:** unavailable", body)
        self.assertIsNone(check.status_execution_record(self.api.status, owner)["num_turns"])
        self.assertEqual(check.status_execution_record(self.api.status, owner)["reason"], "conflicting")
        self.assertEqual(self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=json.dumps(conflicting)), body)

    def test_compact_completion_count_uses_the_captured_owner(self):
        owner = fixtures.presentation_owner()
        record = receipt(owner, reason="execution_observed", count=33, maximum=None)
        for candidate, expected in ((record, "33"), ({**record, "attempt": 2}, "unavailable"), (None, "unavailable")):
            body = check.completion_comment_body(fixtures.REPO, fixtures.HEAD, "main", fixtures.BASE_TIP,
                "synthetic-marker", "https://github.com/owner/repo/actions/runs/5", owner=owner,
                execution_record=candidate)
            visible, details = body.split("<details>", 1)
            self.assertNotIn("**Turns:**", visible)
            self.assertIn("**Turns:** " + expected, details)

    def finalize_failure(self, owner, **changes):
        os.environ.update(REVIEW_RESULT="failure", ACTION_CONCLUSION="",
                          COMPLETION_VERIFIED="", COMPLETION_REASON="",
                          EXECUTION_DIAGNOSTIC=json.dumps(receipt(owner)))
        os.environ.update(changes)
        check.best_effort_status(fixtures.HEAD, "failure")
        return self.api.status["body"]

    def test_turn_limit_copy_and_hidden_receipts_survive_current_and_stale_refresh(self):
        cases = (
            ("over_limit_success", 76, 75, "Claude reported success after 76 turns, exceeding the runtime’s 75-turn limit."),
            ("error_max_turns", 75, 75, "Claude reached the runtime’s 75-turn limit (75 turns reported)."),
            ("error_max_turns", None, 75, "Claude reached the runtime’s 75-turn limit."),
            ("error_max_turns", 75, None, "Claude reported a turn-limit error (75 turns reported)."),
            ("error_max_turns", None, None, "Claude reported a turn-limit error."),
        )
        for kind, count, maximum, expected in cases:
            with self.subTest(kind=kind, count=count, maximum=maximum):
                self.api.status = None
                self.api.pr["head"]["sha"] = fixtures.HEAD
                owner = self.start()
                diagnostic = receipt(owner, reason=kind, count=count, maximum=maximum)
                body = self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=json.dumps(diagnostic))
                for stale in (False, True):
                    with self.subTest(stale=stale):
                        if stale:
                            self.api.pr["head"]["sha"] = fixtures.OTHER
                            check.best_effort_status(fixtures.OTHER, "stale")
                            body = self.api.status["body"]
                        reason = check.STATUS_REASONS["turn_limit"]
                        if stale:
                            reason = check.stale_status_reason(reason)
                        raw_receipt = "<!--\n**Reason:** " + reason + "\n-->"
                        self.assertIn(raw_receipt, body)
                        self.assertEqual(check.terminal_status_reason(self.api.status, owner), reason)
                        self.assertEqual(check.status_execution_diagnostic(self.api.status, owner), diagnostic)
                        visible = re.sub(r"<!--.*?-->", "", body, flags=re.S)
                        summary, details = visible.split("<details>", 1)
                        self.assertIn(expected, summary)
                        self.assertNotIn("**Reason:**", details)
                        paragraphs = [p.strip() for p in details.split("\n\n") if p.strip()]
                        self.assertEqual(paragraphs[0], "<summary>ℹ️ Details</summary>")
                        self.assertTrue(paragraphs[1].startswith("**PR commit:**" if stale else "**Started:**"),
                                        paragraphs[1])
                        if stale:
                            self.assertIn("Current head and base coverage is not established by this attempt.", summary)
                            self.assertIn("— not reviewed", details)
                            check.best_effort_status(fixtures.OTHER, "stale")
                            self.assertEqual(self.api.status["body"], body)

    def test_verified_execution_precedes_workflow_hint_and_keeps_failure(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            if kind != "automatic":
                self.target(kind)
            owner = self.start()
            with mock.patch.object(check, "workflow_changed") as changed:
                body = self.finalize_failure(owner)
            changed.assert_not_called()
            self.assertIn("31 turns", body)
            self.assertIn("the runtime’s 30-turn limit", body)
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
            self.assertIsNone(check.status_execution_diagnostic(self.api.status, owner))
            self.assertNotIn("31 turns", body)

    def test_malformed_missing_and_conflicting_diagnostics_cannot_supply_cause(self):
        owner = self.start()
        for raw in ("", "invalid", json.dumps({**receipt(owner), "attempt": 2}),
                    json.dumps(receipt(owner, reason="malformed", count=None)),
                    json.dumps(receipt(owner, reason="unavailable", count=None))):
            with mock.patch.object(check, "workflow_changed", return_value=None):
                body = self.finalize_failure(owner, EXECUTION_DIAGNOSTIC=raw)
            self.assertNotIn("31 turns", body)
            self.assertIsNone(check.status_execution_diagnostic(self.api.status, owner))
            self.assertIn("**Turns:** unavailable", body)
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
