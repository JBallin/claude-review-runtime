"""Offline lifecycle coverage for timestamps in the shared status comment."""
import json
import os
import unittest

from test_claude_review_check import check, HEAD, OTHER, presentation_owner
import test_presentation_ownership as ownership

START_NS = 1791316369000000000
START = "2026-10-06T19:52:49Z"
DONE = "2026-10-06T19:54:00Z"
LATER = "2026-10-06T20:00:00Z"


class StatusTimestampTests(unittest.TestCase):
    def setUp(self):
        original_path = os.environ["PATH"]
        ownership.OwnershipTests.setUp(self)
        os.environ["PATH"] = original_path
    start = ownership.OwnershipTests.start

    def finish(self, state="success", when=DONE):
        check.best_effort_status(HEAD, state, completed_at=when)

    def body(self):
        return self.api.status["body"]

    def test_start_and_completed_result_have_captured_times(self):
        for state in ("success", "action_required"):
            with self.subTest(state=state):
                self.start(PRESENTATION_START=START_NS)
                self.assertIn("**Started:** " + check.relative_time(START), self.body())
                self.assertNotIn("**Completed:**", self.body())
                self.finish(state)
                self.assertIn("**Completed:** " + check.relative_time(DONE), self.body())
                self.assertEqual(check.last_completed_review_details(self.api.status)["completed_at"], DONE)
                self.finish(state, LATER)
                self.assertIn("**Completed:** " + check.relative_time(DONE), self.body())
                self.start(PRESENTATION_START=START_NS + 10**9, GITHUB_RUN_ID=6)
                self.finish(state, LATER)
                self.assertIn("**Completed:** " + check.relative_time(LATER), self.body())
                os.environ["GITHUB_RUN_ID"] = "5"
                self.api.status = None

    def test_stale_completion_and_refresh_preserve_review_time(self):
        self.start(PRESENTATION_START=START_NS)
        self.api.base_tip = OTHER
        self.finish()
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertIn("**Last review completed:** " + check.relative_time(DONE), self.body())
        check.best_effort_status(HEAD, "stale")
        self.assertIn("**Last review completed:** " + check.relative_time(DONE), self.body())

    def test_new_failed_or_cancelled_attempt_does_not_complete_history(self):
        self.start(PRESENTATION_START=START_NS)
        self.finish()
        for state, result in (("failure", "failure"), ("failure", "cancelled"),
                              ("publication_incomplete", "success")):
            with self.subTest(state=state, result=result):
                self.start(PRESENTATION_START=START_NS + 10**9, GITHUB_RUN_ID=6)
                os.environ["REVIEW_RESULT"] = result
                self.finish(state, LATER)
                self.assertNotIn("**Completed:**", self.body())
                self.assertEqual(check.last_completed_review_details(self.api.status)["completed_at"], DONE)
                self.api.base_tip = OTHER
                check.best_effort_status(HEAD, "stale")
                self.assertIn("**Last review completed:** " + check.relative_time(DONE), self.body())
                self.api.base_tip = os.environ["BASE_SHA"]

    def test_recovered_owner_replaces_prior_review_completion_and_keeps_its_own(self):
        for prior_time in (DONE, None):
            for failed_state in ("failure", "publication_incomplete"):
                for outcome in ("success", "action_required"):
                    with self.subTest(prior_time=prior_time, failed_state=failed_state, outcome=outcome):
                        self.api.status = None
                        os.environ.update(GITHUB_RUN_ID="5", REVIEW_RESULT="success",
                                          DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                        owner_a = self.start(PRESENTATION_START=START_NS)
                        self.finish(outcome, prior_time)
                        owner_b = self.start(PRESENTATION_START=START_NS + 120 * 10**9,
                            GITHUB_RUN_ID=6, DETAILS_URL="https://github.com/owner/repo/actions/runs/6")
                        self.finish(failed_state, LATER)
                        historical = check.last_completed_review_details(self.api.status)
                        self.assertEqual(historical.get("completed_at"), prior_time)
                        self.assertEqual(historical.get("generation"), owner_a["generation"])
                        self.assertEqual(historical["run_url"], "https://github.com/owner/repo/actions/runs/5")
                        self.finish(outcome, LATER)
                        completed = check.last_completed_review_details(self.api.status)
                        self.assertEqual(completed["completed_at"], LATER)
                        self.assertEqual(completed["generation"], owner_b["generation"])
                        self.assertEqual(completed["run_url"], "https://github.com/owner/repo/actions/runs/6")
                        self.finish(outcome, "2026-10-06T20:10:00Z")
                        self.assertEqual(check.last_completed_review_details(self.api.status)["completed_at"], LATER)

    def test_legacy_terminal_result_remains_undated_on_repeat_finalization(self):
        self.start(PRESENTATION_START=START_NS)
        self.finish(when=None)
        self.finish(when=LATER)
        self.assertNotIn("**Completed:**", self.body())
        self.assertNotIn("completed_at", check.last_completed_review_details(self.api.status))

    def test_legacy_undated_completion_survives_same_owner_stale_or_failure(self):
        for transition in ("stale", "failure", "publication_incomplete"):
            for outcome in ("success", "action_required"):
                with self.subTest(transition=transition, outcome=outcome):
                    self.api.status = None
                    owner = self.start(PRESENTATION_START=START_NS)
                    self.finish(outcome, None)
                    # Simulate deployed history with no timestamp/provenance.
                    self.api.status["body"] = "\n".join(line for line in self.body().splitlines()
                        if not line.startswith(check.STATUS_REVIEWED_GENERATION_PREFIX))
                    self.assertNotIn("completed_at", check.last_completed_review_details(self.api.status))
                    if transition == "stale":
                        self.api.base_tip = OTHER
                        check.best_effort_status(HEAD, "stale")
                        self.api.base_tip = os.environ["BASE_SHA"]
                    else:
                        self.finish(transition, LATER)
                    self.assertEqual(check.status_state(self.api.status), transition)
                    self.assertEqual(check.last_completed_review_details(self.api.status)["generation"], owner["generation"])
                    self.assertNotIn("**Last review completed:**", self.body())
                    self.finish(outcome, LATER)
                    self.assertEqual(check.status_state(self.api.status), outcome)
                    self.assertNotIn("**Completed:**", self.body())
                    self.assertNotIn("completed_at", check.last_completed_review_details(self.api.status))

    def test_timestamp_metadata_rejects_invalid_or_duplicate_values(self):
        for value in ('bad', '2026-02-31T19:54:00Z', '2026-10-06T19:54:00Z" onclick="bad'):
            comment = {"body": check.STATUS_REVIEWED_COMPLETED_PREFIX + value + " -->"}
            self.assertEqual(check.last_completed_review_details(comment), {})
        marker = check.STATUS_REVIEWED_COMPLETED_PREFIX + DONE + " -->"
        self.assertEqual(check.last_completed_review_details({"body": marker + "\n" + marker}), {})

    def test_old_owner_cannot_replace_new_timestamps(self):
        owner = self.start(PRESENTATION_START=START_NS)
        self.start(PRESENTATION_START=START_NS + 10**9, GITHUB_RUN_ID=6)
        self.finish(when=LATER)
        body = self.body()
        os.environ.update(GITHUB_RUN_ID="5", PRESENTATION_OWNER=json.dumps(owner))
        self.finish()
        self.assertEqual(self.body(), body)

    def test_legacy_undated_emergency_shell_then_helper_keeps_completion_undated(self):
        from test_claude_review_workflows import ToolingFallbackScriptTests, AUTOMATIC, MANUAL, REPO
        for workflow in (AUTOMATIC, MANUAL):
            for outcome in ("success", "action_required"):
                with self.subTest(workflow=workflow.name, outcome=outcome):
                    self.api.status = None
                    # The stateful helper API uses a fixed fixture repository;
                    # the shell fixture accepts that same captured repository.
                    owner = self.start(PRESENTATION_START=START_NS)
                    self.finish(outcome, None)
                    self.api.status["body"] = "\n".join(line for line in self.body().splitlines()
                        if not line.startswith(check.STATUS_REVIEWED_GENERATION_PREFIX))
                    run, calls = ToolingFallbackScriptTests().run_fallback(workflow,
                        OWNER_FIXTURE=owner, REPO=owner["repo"], PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={
                            "STATUS":[{"stdout":json.dumps(self.api.status)}],
                            "PR":[{"stdout":json.dumps(self.api.pr)}]})
                    self.assertEqual(run.returncode, 1, run.stderr)
                    repaired = next(c["body"]["body"] for c in calls if c["method"] == "PATCH"
                                    and any("issues/comments/55" in a for a in c["args"]))
                    self.api.status["body"] = repaired
                    self.finish(outcome, LATER)
                    self.assertEqual(check.status_state(self.api.status), outcome)
                    self.assertNotIn("**Completed:**", self.body())
                    self.assertNotIn("completed_at", check.last_completed_review_details(self.api.status))

    def test_emergency_legacy_migration_rejects_duplicate_untrusted_or_other_metadata(self):
        import copy
        import re
        import subprocess
        import sys
        import textwrap
        from test_claude_review_workflows import AUTOMATIC, MANUAL
        owner = presentation_owner(started=START_NS)
        body = check.status_comment_body(HEAD, "main", "success", owner=owner)
        body = "\n".join(line for line in body.splitlines()
            if not line.startswith(check.STATUS_REVIEWED_GENERATION_PREFIX))
        original = {"user":{"login":check.STATUS_AUTHOR,"type":"Bot"}, "body":body}
        for workflow in (AUTOMATIC, MANUAL):
            script = re.search(r"legacy_generation=.*?python3 -c '(.*?)' \"\$PRESENTATION_OWNER\"",
                               workflow.read_text(), re.DOTALL).group(1)
            script = textwrap.dedent(script)
            for mutation in ("valid", "duplicate_result", "other_head", "other_base", "other_run", "untrusted", "duplicate_state", "duplicate_phase"):
                with self.subTest(workflow=workflow.name, mutation=mutation):
                    fixture = copy.deepcopy(original)
                    if mutation == "duplicate_result":
                        fixture["body"] += "\n" + check.STATUS_REVIEWED_RESULT_PREFIX + "success -->"
                    elif mutation == "other_head":
                        fixture["body"] = fixture["body"].replace(check.STATUS_REVIEWED_HEAD_PREFIX + HEAD,
                            check.STATUS_REVIEWED_HEAD_PREFIX + OTHER)
                    elif mutation == "other_base":
                        fixture["body"] += "\n" + check.STATUS_REVIEWED_BASE_SHA_PREFIX + OTHER + " -->"
                    elif mutation == "other_run":
                        fixture["body"] += "\n" + check.STATUS_REVIEWED_RUN_URL_PREFIX + check.quote("https://github.com/owner/repo/actions/runs/99", safe="") + " -->"
                    elif mutation == "untrusted":
                        fixture["user"]["login"] = "someone"
                    elif mutation == "duplicate_state":
                        fixture["body"] += "\n" + check.STATUS_STATE_PREFIX + "failure -->"
                    elif mutation == "duplicate_phase":
                        fixture["body"] += "\n" + check.OWNER_PHASE_PREFIX + "running -->"
                    historical = "\n".join(line for line in fixture["body"].splitlines()
                        if line.startswith("<!-- claude-review-runtime:claude-review-last-reviewed-"))
                    run = subprocess.run([sys.executable, "-c", script, json.dumps(owner), "https://github.com", historical],
                                         input=json.dumps(fixture), capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    expected = check.STATUS_REVIEWED_GENERATION_PREFIX + owner["generation"] + " -->\n"
                    self.assertEqual(run.stdout, expected if mutation == "valid" else "")

    def test_emergency_stale_keeps_completion_but_does_not_date_legacy_history(self):
        from test_claude_review_workflows import ToolingFallbackScriptTests, AUTOMATIC, MANUAL, REPO, pr_identity
        for workflow in (AUTOMATIC, MANUAL):
            for value in (DONE, None, "2026-02-31T19:54:00Z"):
                with self.subTest(workflow=workflow.name, value=value):
                    owner = presentation_owner(repo=REPO, started=START_NS)
                    body = check.status_comment_body(HEAD, "main", "in_progress", owner=owner,
                        last_review=(HEAD, "main", "success"), last_review_details={"completed_at": value})
                    if value and value != DONE:
                        body += "\n" + check.STATUS_REVIEWED_COMPLETED_PREFIX + value + " -->"
                    status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"}, "body":body}
                    run, calls = ToolingFallbackScriptTests().run_fallback(workflow,
                        OWNER_FIXTURE=owner, PR_NUMBER="7", STATUS_COMMENT_ID="55", responses={
                            "STATUS":[{"stdout":json.dumps(status)}], "PR":[{"stdout":pr_identity(OTHER)}]})
                    self.assertEqual(run.returncode, 1, run.stderr)
                    repaired = next(c["body"]["body"] for c in calls if c["method"] == "PATCH"
                                    and any("issues/comments/55" in a for a in c["args"]))
                    self.assertIn("**Started:** " + check.relative_time(START), repaired)
                    self.assertEqual("**Last review completed:**" in repaired, value == DONE)
                    self.assertNotIn("**Completed:**", repaired)


class FinalCheckTimestampTests(unittest.TestCase):
    def test_completion_time_matches_the_published_check(self):
        import test_claude_review_check as fixtures
        harness = fixtures.ScriptTestCase()
        harness.setUp()
        try:
            owner = fixtures.presentation_owner(started=START_NS)
            status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"},
                      "body":check.status_comment_body(HEAD,"main","in_progress",owner=owner)}
            harness.before.write_text("[]")
            result = harness.run_script(["finalize"], [
                fixtures.history_rule(fixtures.current_run_only()),
                fixtures.comments_rule(fixtures.ok(fixtures.pages([]))),
                fixtures.patch_rule(fixtures.ok("{}")),
                fixtures.status_list_rule(fixtures.ok(fixtures.pages([status]))),
                fixtures.status_patch_rule(fixtures.ok("{}")),
            ], CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                BEFORE_IDS_FILE=str(harness.before), STATUS_COMMENTS_ENABLED="true",
                PRESENTATION_OWNER=json.dumps(owner))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            patches = harness.calls("PATCH")
            final_check = next(c["body"] for c in patches if "/check-runs/" in c["path"])
            final_comment = next(c["body"] for c in patches if "/issues/comments/" in c["path"])
            self.assertEqual(check.last_completed_review_details(final_comment)["completed_at"],
                             final_check["completed_at"])
            self.assertIn("**Completed:** " + check.relative_time(final_check["completed_at"]),
                          final_comment["body"])
        finally:
            harness.tearDown()

    def test_finalize_recovery_dates_the_current_owner_after_prior_failed_presentation(self):
        import test_claude_review_check as fixtures
        for prior_time in (DONE, None):
            for failed_state in ("failure", "publication_incomplete"):
                for outcome in ("success", "action_required"):
                    with self.subTest(prior_time=prior_time, failed_state=failed_state, outcome=outcome):
                        harness = fixtures.ScriptTestCase()
                        harness.setUp()
                        try:
                            owner_a = fixtures.presentation_owner(started=START_NS)
                            owner_b = fixtures.presentation_owner(started=START_NS + 120 * 10**9, run=6)
                            historical = {"completed_at": prior_time,
                                          "generation": owner_a["generation"] if prior_time else None,
                                          "run_url": "https://github.com/owner/repo/actions/runs/5"}
                            status = {"id":55, "user":{"login":check.STATUS_AUTHOR,"type":"Bot"},
                                      "body":check.status_comment_body(HEAD,"main",failed_state,owner=owner_b,
                                          last_review=(HEAD,"main",outcome),last_review_details=historical)}
                            harness.before.write_text("[]")
                            findings = [fixtures.comment(9)] if outcome == "action_required" else []
                            result = harness.run_script(["finalize"], [
                                fixtures.history_rule(fixtures.current_run_only()),
                                fixtures.comments_rule(fixtures.ok(fixtures.pages(findings))),
                                fixtures.patch_rule(fixtures.ok("{}")),
                                fixtures.status_list_rule(fixtures.ok(fixtures.pages([status]))),
                                fixtures.status_patch_rule(fixtures.ok("{}")),
                            ], CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                                BEFORE_IDS_FILE=str(harness.before), STATUS_COMMENTS_ENABLED="true",
                                GITHUB_RUN_ID="6", PRESENTATION_OWNER=json.dumps(owner_b),
                                DETAILS_URL="https://github.com/owner/repo/actions/runs/6")
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                            patches = harness.calls("PATCH")
                            final_check = next(c["body"] for c in patches if "/check-runs/" in c["path"])
                            final_comment = next(c["body"] for c in patches if "/issues/comments/" in c["path"])
                            self.assertEqual(final_check["conclusion"], outcome)
                            details = check.last_completed_review_details(final_comment)
                            self.assertEqual(details["completed_at"], final_check["completed_at"])
                            self.assertEqual(details["generation"], owner_b["generation"])
                            self.assertEqual(details["run_url"], "https://github.com/owner/repo/actions/runs/6")
                        finally:
                            harness.tearDown()
