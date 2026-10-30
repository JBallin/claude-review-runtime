"""Executed Runtime display follows trusted attempt/history ownership, never refresh configuration."""
import copy
import os
import unittest

import test_presentation_ownership as ownership
from test_claude_review_check import check, HEAD, OTHER, NEWER, BASE_TIP, REPO


class RuntimeProvenanceTests(unittest.TestCase):
    setUp = ownership.OwnershipTests.setUp
    start = ownership.OwnershipTests.start
    finish = ownership.OwnershipTests.finish

    def configure(self, sha="f" * 40):
        os.environ.update(RUNTIME_REPOSITORY="JBallin/claude-review-runtime", RUNTIME_SHA=sha,
                          RUNTIME_WORKFLOW_PATH=".github/workflows/claude.yml")
        return check.executing_runtime()

    def test_current_states_link_known_runtime_only_in_details(self):
        runtime = self.configure()
        owner = check.captured_owner()
        for state in ("in_progress", "success", "action_required", "failure", "publication_incomplete"):
            with self.subTest(state=state):
                body = check.status_comment_body(HEAD, "main", state, owner=owner, runtime=runtime)
                visible, details = body.split("<details>", 1)
                self.assertNotIn("**Runtime used:**", visible)
                self.assertEqual(details.count(check.runtime_used_line(runtime)), 1)
                self.assertNotIn("proposed pin", body)
                unknown = check.status_comment_body(HEAD, "main", state, owner=owner)
                self.assertNotIn("**Runtime used:**", unknown)

    def test_manual_settings_and_runtime_provenance_keep_their_own_locations(self):
        runtime = self.configure()
        owner = check.captured_owner()
        selection = {"generation": owner["generation"], "requested_model": "opus", "requested_effort": "high",
                     "models": ["claude-opus-5-5"], "model_evidence": "observed", "execution_attempt": 1,
                     "observed_effort": None, "primary_model_evidence": {
                         "initialization": "claude-opus-5-5", "assistant": ["claude-opus-5-5"], "attempt": 1}}
        body = check.completion_comment_body(REPO, HEAD, "main", BASE_TIP,
            check.completion_marker(REPO, 7, HEAD, "main", BASE_TIP, "c" * 40),
            "https://github.com/owner/repo/actions/runs/5", selection=selection)
        visible, details = body.split("<details>", 1)
        reviewed = "**Reviewed commit:** " + check.commit_link(HEAD, REPO)
        self.assertIn(reviewed, visible)
        self.assertNotIn("**Model:**", visible)
        self.assertIn("**Model:** Opus 5.5 · **Effort:** High", details)
        self.assertNotIn("Runtime used", visible)
        self.assertEqual(details.count(check.runtime_used_line(runtime)), 1)

    def test_stale_refresh_preserves_executed_runtime_not_refresher(self):
        executed = self.configure()
        owner = self.start()
        self.finish()
        refresher = self.configure("c" * 40)
        self.api.pr["head"]["sha"] = OTHER
        check.best_effort_status(OTHER, "stale")
        self.assertIn(check.runtime_used_line(executed), self.api.status["body"])
        self.assertNotIn(check.runtime_used_line(refresher), self.api.status["body"])
        self.assertEqual(check.status_runtime(self.api.status, owner), executed)
        self.assertEqual(check.last_completed_review_details(self.api.status)["runtime"], executed)

    def test_inline_failure_refresh_retains_its_count_and_executed_runtime(self):
        executed = self.configure()
        owner = self.start()
        os.environ.update(REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                          COMPLETION_VERIFIED="false", COMPLETION_REASON="errored_inline_tool_result")
        check.best_effort_status(HEAD, "failure", inline_failure={"check_run_id": 99, "posted_count": 2})
        self.assertEqual(check.status_inline_failure(self.api.status, owner)["posted_count"], 2)
        self.assertEqual(check.status_runtime(self.api.status, owner), executed)
        self.assertIn("check_run_id=99", self.api.status["body"])
        refresher = self.configure("c" * 40)
        self.api.pr["head"]["sha"] = OTHER
        os.environ.update(COMPLETION_REASON="verified", COMPLETION_VERIFIED="true")
        check.best_effort_status(OTHER, "stale")
        self.assertEqual(check.status_inline_failure(self.api.status, owner)["posted_count"], 2)
        self.assertEqual(check.status_runtime(self.api.status, owner), executed)
        self.assertNotIn(check.runtime_used_line(refresher), self.api.status["body"])
        self.assertNotIn("request an approved fresh", self.api.status["body"])

    def test_new_attempt_and_previous_review_keep_distinct_runtime_provenance(self):
        previous = self.configure()
        self.start()
        self.finish()
        current = self.configure("c" * 40)
        self.api.pr["head"]["sha"] = OTHER
        os.environ.update(HEAD_SHA=OTHER, GITHUB_RUN_ID="6", PRESENTATION_START="3")
        check.best_effort_status(OTHER, "in_progress", acquire=True)
        owner = check.status_owner(self.api.status)
        body = self.api.status["body"]
        attempt, history = body.split("### Previous completed review", 1)
        self.assertIn(check.runtime_used_line(current), attempt)
        self.assertNotIn(check.runtime_used_line(previous), attempt)
        self.assertIn(check.runtime_used_line(previous), history)
        self.assertEqual(check.status_runtime(self.api.status, owner), current)
        self.assertEqual(check.last_completed_review_details(self.api.status)["runtime"], previous)
        self.configure("e" * 40)
        self.api.pr["head"]["sha"] = NEWER
        check.best_effort_status(NEWER, "stale")
        self.assertEqual(check.status_runtime(self.api.status, owner), current)
        self.assertEqual(check.last_completed_review_details(self.api.status)["runtime"], previous)

    def test_missing_current_provenance_never_steals_previous_runtime(self):
        previous = self.configure()
        self.start()
        self.finish()
        os.environ.pop("RUNTIME_SHA")
        self.api.pr["head"]["sha"] = OTHER
        os.environ.update(HEAD_SHA=OTHER, GITHUB_RUN_ID="6", PRESENTATION_START="3")
        check.best_effort_status(OTHER, "in_progress", acquire=True)
        owner = check.status_owner(self.api.status)
        self.assertIsNone(check.status_runtime(self.api.status, owner))
        self.assertEqual(check.last_completed_review_details(self.api.status)["runtime"], previous)

    def test_legacy_and_small_history_headings_keep_both_runtime_links_on_refresh(self):
        previous = self.configure()
        self.start()
        self.finish()
        current = self.configure("c" * 40)
        self.api.pr["head"]["sha"] = OTHER
        os.environ.update(HEAD_SHA=OTHER, GITHUB_RUN_ID="6", PRESENTATION_START="3")
        check.best_effort_status(OTHER, "in_progress", acquire=True)
        original = copy.deepcopy(self.api.status)
        owner = check.status_owner(original)
        self.configure("e" * 40)
        self.api.pr["head"]["sha"] = NEWER
        for heading_format in ("current", "level-four", "bold"):
            with self.subTest(heading_format=heading_format):
                self.api.status = copy.deepcopy(original)
                if heading_format != "current":
                    for label in ("Current attempt", "Previous completed review"):
                        self.api.status["body"] = self.api.status["body"].replace(
                            "### " + label + "\n",
                            "#### " + label + "\n\n" if heading_format == "level-four" else "**" + label + "**  \n")
                self.assertEqual(check.status_runtime(self.api.status, owner), current)
                self.assertEqual(check.status_runtime(self.api.status, owner, previous=True), previous)
                check.best_effort_status(NEWER, "stale")
                self.assertEqual(check.status_runtime(self.api.status, owner), current)
                self.assertEqual(check.last_completed_review_details(self.api.status)["runtime"], previous)

    def test_untrusted_ambiguous_or_noncanonical_display_is_not_provenance(self):
        runtime = self.configure()
        owner = self.start()
        original = copy.deepcopy(self.api.status)
        line = check.runtime_used_line(runtime)
        for mutation in ("duplicate", "label", "revision", "author", "owner"):
            with self.subTest(mutation=mutation):
                comment = copy.deepcopy(original)
                if mutation == "duplicate": comment["body"] += "\n" + line
                elif mutation == "label": comment["body"] = comment["body"].replace("@fffffff", "@main")
                elif mutation == "revision": comment["body"] = comment["body"].replace("/blob/" + "f" * 40, "/blob/main")
                elif mutation == "author": comment["user"] = {"login": "outsider", "type": "User"}
                else: comment["body"] = comment["body"].replace(owner["generation"], "0" * 64)
                self.assertIsNone(check.status_runtime(comment, owner))
