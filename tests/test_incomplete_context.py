"""Offline checks for bounded, run-bound workflow-change context."""

import copy
import json
import os
import unittest
from unittest import mock

from test_claude_review_check import check as context
from test_claude_review_check import HEAD as CAPTURED_HEAD, OTHER
import test_presentation_ownership as ownership


REPO = "owner/repo"
BASE, HEAD, RUNTIME = "a" * 40, "b" * 40, "c" * 40
OWNER = {"repo": REPO, "head": HEAD, "base": BASE, "run": 5, "attempt": 2}
CALLER = ".github/workflows/review-auto.yml"
ENTRYPOINT = ".github/workflows/claude-review.yml"
WORKFLOW = ".github/workflows/claude.yml"


class WorkflowContextTests(unittest.TestCase):
    def setUp(self):
        self.run = {
            "id": 5, "run_attempt": 2, "repository": {"full_name": REPO},
            "path": CALLER,
            "referenced_workflows": [{"path": f"{REPO}/{ENTRYPOINT}@{RUNTIME}", "sha": RUNTIME}],
        }
        self.comparison = {"files": [{"filename": CALLER}]}
        self.calls = []

    def api(self, args):
        self.calls.append(args)
        if "/actions/runs/" in args[0]:
            return json.dumps(self.run)
        return json.dumps(self.comparison)

    def changed(self, *, owner=None, runtime_repo=REPO, runtime_path=WORKFLOW):
        return context.workflow_changed(owner or OWNER, runtime_repo, runtime_path, self.api)

    def test_automatic_and_manual_caller_paths_with_refs_are_relevant(self):
        for caller in (CALLER, ".github/workflows/review-manual.yaml@refs/heads/main"):
            with self.subTest(caller=caller):
                self.run["path"] = caller
                self.comparison["files"] = [{"filename": caller.split("@")[0]}]
                self.assertIs(self.changed(), True)

    def test_actual_automatic_and_manual_runtime_paths_are_relevant(self):
        for path in (ENTRYPOINT, WORKFLOW):
            with self.subTest(path=path):
                self.comparison["files"] = [{"filename": path}]
                self.assertIs(self.changed(runtime_path=path), True)

    def test_run_wide_references_do_not_identify_review_invocation_chain(self):
        self.comparison["files"] = [{"filename": ENTRYPOINT}]
        self.assertIs(self.changed(), False)
        self.run["referenced_workflows"] = None
        self.assertIs(self.changed(), False)

    def test_pin_change_is_identified_by_actual_caller_path(self):
        self.comparison["files"][0]["patch"] = "+ uses: owner/runtime/.github/workflows/review.yml@newpin"
        self.assertIs(self.changed(), True)

    def test_unrelated_workflows_and_other_files_are_not_relevant(self):
        self.comparison["files"] = [{"filename": ".github/workflows/ci.yml"}, {"filename": "src/review.py"}]
        self.assertIs(self.changed(), False)
        self.comparison["files"] = []
        self.assertIs(self.changed(), False)

    def test_foreign_runtime_same_named_workflow_is_not_a_local_change(self):
        self.run["referenced_workflows"][0]["path"] = f"owner/runtime/{ENTRYPOINT}@{RUNTIME}"
        self.comparison["files"] = [{"filename": ENTRYPOINT}, {"filename": WORKFLOW}]
        self.assertIs(self.changed(runtime_repo="owner/runtime"), False)
        self.comparison["files"] = [{"filename": CALLER}]
        self.assertIs(self.changed(runtime_repo="owner/runtime"), True)

    def test_renamed_or_deleted_relevant_workflow_is_detected(self):
        self.comparison["files"] = [{"filename": "archive/review.yml", "previous_filename": CALLER}]
        self.assertIs(self.changed(), True)
        self.comparison["files"] = [{"filename": CALLER, "status": "removed"}]
        self.assertIs(self.changed(), True)

    def test_run_attempt_and_repository_conflicts_are_unknown(self):
        for key, value in (("id", 6), ("id", True), ("run_attempt", 1),
                           ("repository", {"full_name": "other/repo"}), ("repository", None)):
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.run)
                self.run[key] = value
                self.assertIsNone(self.changed())
                self.run = original

    def test_missing_or_unsafe_caller_is_unknown_without_comparing(self):
        for path in (None, "", "../.github/workflows/review.yml", ".github/workflows/../review.yml",
                     ".github/workflows/foo.yml@", ".github/workflows/foo.yml@bad\nref", "src/review.py"):
            with self.subTest(path=path):
                self.calls.clear()
                self.run["path"] = path
                self.assertIsNone(self.changed())
                self.assertEqual(len(self.calls), 1)

    def test_truncated_and_malformed_file_data_stays_unknown(self):
        for files in (None, {}, [None], [{}], [{"filename": "../review.yml"}],
                      [{"filename": CALLER, "previous_filename": False}],
                      [{"filename": CALLER}] * 300):
            with self.subTest(files=files):
                self.comparison["files"] = files
                self.assertIsNone(self.changed())

    def test_unavailable_or_invalid_json_is_unknown_and_never_retried(self):
        def unavailable(args):
            self.calls.append(args)
            raise RuntimeError("unavailable")
        self.assertIsNone(context.workflow_changed(OWNER, REPO, WORKFLOW, unavailable))
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(context.workflow_changed(OWNER, REPO, WORKFLOW, lambda _: "not json"))
        self.assertIsNone(context.workflow_changed(OWNER, REPO, WORKFLOW, lambda _: "[]"))

    def test_invalid_captured_identity_performs_no_api_reads(self):
        for key, value in (("repo", "../repo"), ("head", "main"), ("base", "HEAD"),
                           ("run", True), ("attempt", 0)):
            with self.subTest(key=key, value=value):
                self.assertIsNone(self.changed(owner={**OWNER, key: value}))
        self.assertEqual(self.calls, [])

    def test_two_reads_are_immutable_run_bound_and_non_paginated(self):
        self.assertIs(self.changed(), True)
        self.assertEqual(self.calls, [
            [f"repos/{REPO}/actions/runs/5/attempts/2"],
            [f"repos/{REPO}/compare/{BASE}...{HEAD}"],
        ])


class IncompletePresentationTests(unittest.TestCase):
    setUp = ownership.OwnershipTests.setUp
    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target

    def test_recovery_links_follow_main_while_patch_and_run_links_stay_captured(self):
        owner = self.start()
        guide = "https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md"
        for key, anchor in (("incomplete", "installation-and-incomplete-reviews"),
                            ("unread_inputs", "incomplete-captured-input-reads"),
                            ("workflow_changed", "installation-and-incomplete-reviews")):
            with self.subTest(reason=key):
                body = context.status_comment_body(CAPTURED_HEAD, "main", "failure",
                                                   owner=owner, reason=context.STATUS_REASONS[key])
                self.assertIn(guide + "#" + anchor, body.split("<details>", 1)[0])
                self.assertIn(context.commit_link(CAPTURED_HEAD), body)
                self.assertEqual(context.status_owner({"body": body}), owner)
                if key == "workflow_changed":
                    self.assertIn("https://github.com/owner/repo/actions/runs/5/attempts/1", body)
        _, _, summary = context.decide("success", "success", [], [], [],
                                      completion_reason="captured_inputs_not_read")
        self.assertIn(guide + "#incomplete-captured-input-reads", summary)

    def test_unread_input_reason_and_direct_recovery_override_path_context(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for changed in (True, False, None):
                with self.subTest(kind=kind, changed=changed):
                    self.api.status = None
                    os.environ.pop("TRIGGER_COMMENT_ID", None)
                    if kind != "automatic":
                        self.target(kind)
                    os.environ.update(TRIGGER_KIND=kind,
                                      REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                                      COMPLETION_VERIFIED="false", COMPLETION_REASON="captured_inputs_not_read")
                    owner = self.start()
                    with mock.patch.object(context, "workflow_changed", return_value=changed) as paths:
                        context.best_effort_status(CAPTURED_HEAD, "failure")
                    paths.assert_not_called()
                    body = self.api.status["body"]
                    visible, details = body.split("<details>", 1)
                    self.assertIn(context.STATUS_REASONS["unread_inputs"], visible)
                    self.assertIn(context.UNREAD_RECOVERY, visible)
                    self.assertNotIn("validation rejection", visible)
                    self.assertNotIn("likely caused", visible)
                    self.assertNotIn("`/claude-review`", visible)
                    self.assertIn("**Reason:** " + context.STATUS_REASONS["unread_inputs"], details)
                    self.assertEqual(context.status_owner(self.api.status), owner)
                    self.assertEqual(context.status_state(self.api.status), "failure")

    def test_bounded_context_warns_only_for_incomplete_relevant_paths(self):
        for kind in ("automatic", "issue_comment", "pull_request_review_comment"):
            for changed in (True, False, None):
                with self.subTest(kind=kind, changed=changed):
                    self.api.status = None
                    os.environ.pop("TRIGGER_COMMENT_ID", None)
                    if kind != "automatic":
                        self.target(kind)
                    os.environ.update(TRIGGER_KIND=kind,
                                      COMPLETION_VERIFIED="false", COMPLETION_REASON="missing_execution_file")
                    self.start()
                    with mock.patch.object(context, "workflow_changed", return_value=changed) as paths:
                        context.best_effort_status(CAPTURED_HEAD, "failure")
                    paths.assert_called_once()
                    visible = self.api.status["body"].split("<details>", 1)[0]
                    self.assertEqual("likely caused" in visible, changed is True)
                    self.assertEqual("`/claude-review`" in visible, changed is True)
                    self.assertIn(context.INCOMPLETE_RECOVERY, visible)
                    self.assertNotIn("expected", visible)
                    if changed:
                        self.assertIn("to confirm.", visible)
                        self.assertIn("If this repository has the supported trusted manual caller", visible)
                        self.assertIn("an authorized user", visible)
                        self.assertIn("an eligible PR", visible)
                        self.assertIn("no applicable review is pending or complete", visible)
                        self.assertIn("/actions/runs/5/attempts/1", visible)
                        self.assertIn("**Reason:** " + context.STATUS_REASONS["workflow_changed"],
                                      self.api.status["body"])

    def test_conflicting_or_unavailable_skip_signal_never_claims_rejection(self):
        for changes in ({"COMPLETION_VERIFIED": "true", "COMPLETION_REASON": "captured_inputs_not_read"},
                        {"COMPLETION_VERIFIED": "true", "COMPLETION_REASON": "verified"},
                        {"COMPLETION_VERIFIED": "false", "COMPLETION_REASON": "errored_inline_tool_result"}):
            self.api.status = None
            # This upstream signal is not exported by the installed dependency;
            # ambient or model text must not create the separate #9 diagnosis.
            os.environ.update(SKIPPED_DUE_TO_WORKFLOW_VALIDATION_MISMATCH="true", **changes)
            self.start()
            with mock.patch.object(context, "workflow_changed", return_value=True) as paths:
                context.best_effort_status(CAPTURED_HEAD, "failure")
            paths.assert_not_called()
            visible = self.api.status["body"].split("<details>", 1)[0]
            self.assertIn(context.STATUS_REASONS["incomplete"], visible)
            self.assertNotIn("validation rejection", visible)
            self.assertNotIn("likely caused", visible)
            self.assertNotIn("`/claude-review`", visible)
            self.assertNotIn(context.STATUS_REASONS["unread_inputs"], visible)

    def test_success_findings_running_and_publication_failure_do_not_inspect_paths(self):
        for state in ("success", "action_required", "in_progress", "publication_incomplete"):
            self.api.status = None
            self.start()
            with mock.patch.object(context, "workflow_changed", return_value=True) as paths:
                context.best_effort_status(CAPTURED_HEAD, state)
            paths.assert_not_called()
            self.assertNotIn("validation rejection", self.api.status["body"])
            self.assertNotIn("likely caused", self.api.status["body"])
            self.assertNotIn("`/claude-review`", self.api.status["body"])

    def test_stale_refresh_retains_only_the_same_owners_reason_without_new_inspection(self):
        for key in ("unread_inputs", "workflow_changed", "incomplete"):
            self.api.status = None
            self.api.pr["head"]["sha"] = CAPTURED_HEAD
            owner = self.start()
            self.api.status["body"] = context.status_comment_body(
                CAPTURED_HEAD, "main", "failure", owner=owner, reason=context.STATUS_REASONS[key])
            self.api.pr["head"]["sha"] = OTHER
            with mock.patch.object(context, "workflow_changed") as paths:
                context.best_effort_status(OTHER, "stale")
                context.best_effort_status(OTHER, "stale")
            paths.assert_not_called()
            body = self.api.status["body"]
            visible = body.split("<details>", 1)[0]
            self.assertEqual(context.status_owner(self.api.status), owner)
            self.assertEqual(context.status_head(self.api.status), OTHER)
            self.assertIn(context.commit_link(CAPTURED_HEAD), visible)
            self.assertIn("| ❌ **Incomplete**", visible)
            self.assertIn("**Reason:** " + context.STATUS_REASONS["stale"] + " " + context.STATUS_REASONS[key], body)
            self.assertEqual("likely caused" in visible, key == "workflow_changed")
            self.assertNotIn("`/claude-review`", visible)

    def test_unread_check_summary_remains_failed_and_retains_findings(self):
        conclusion, _, sentence = context.decide("success", "success", [12], [13], [14],
                                                completion_verified=False,
                                                completion_reason="captured_inputs_not_read")
        self.assertEqual(conclusion, "failure")
        self.assertIn(context.UNREAD_RECOVERY, sentence)
        self.assertIn("stay recorded", sentence)
        self.assertIn("earlier Claude finding", sentence)


if __name__ == "__main__":
    unittest.main()
