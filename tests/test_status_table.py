"""One visible status row retains the event that produced its completed result."""
import copy
import os
import unittest

from test_claude_review_check import check, HEAD, OTHER
import test_presentation_ownership as ownership

DONE = "2026-10-07T13:09:19Z"
HEADER = "| Status | Commit | Review trigger |"


class StatusTableTests(unittest.TestCase):
    def setUp(self):
        original_path = os.environ["PATH"]
        ownership.OwnershipTests.setUp(self)
        os.environ["PATH"] = original_path
    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target

    def finish(self, result="success"):
        check.best_effort_status(HEAD, result, completed_at=DONE)

    def visible(self):
        return self.api.status["body"].split("<details>")[0]

    def row(self):
        visible = self.visible()
        self.assertEqual(visible.count(HEADER), 1)
        rows = [line for line in visible.splitlines()
                if line.startswith("| ") and line not in (HEADER, "| --- | --- | --- |")]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_original_event_survives_head_base_and_target_movement(self):
        for kind, label in (("automatic", "PR opened for review"),
                            ("automatic", "Draft marked ready"),
                            ("issue_comment", "Manual request"),
                            ("pull_request_review_comment", "Manual request")):
            for movement in ("head", "base", "ref"):
                for result in ("success", "action_required"):
                    with self.subTest(kind=kind, label=label, movement=movement, result=result):
                        self.api.status = None
                        self.api.pr["head"]["sha"] = HEAD
                        self.api.pr["base"]["ref"] = "main"
                        self.api.base_tip = os.environ["BASE_SHA"]
                        os.environ.update(BASE_REF="main", TRIGGER_KIND="automatic",
                                          PR_REACTION_MODE="automatic", TRIGGER_LABEL=label,
                                          DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                        if kind != "automatic":
                            self.target(kind)
                        self.start()
                        self.finish(result)
                        expected = ("✅ **Completed**" if result == "success" else "⚠️ **Findings**")
                        self.assertEqual(self.row(), f"| {expected} {check.relative_time(DONE)} | `{HEAD[:7]}` | {label} |")
                        if movement == "head":
                            self.api.pr["head"]["sha"] = OTHER
                        elif movement == "base":
                            self.api.base_tip = OTHER
                        else:
                            self.api.pr["base"]["ref"] = "release"
                            os.environ["BASE_REF"] = "release"
                        for refresh_label in ("synchronize", "Manual request", "Draft marked ready"):
                            os.environ["TRIGGER_LABEL"] = refresh_label
                            check.best_effort_status(self.api.pr["head"]["sha"], "stale")
                            self.assertIn(expected, self.row())
                            self.assertNotIn("**Last completed review**", self.visible())
                            self.assertNotIn("Last completed review", self.row())
                            self.assertTrue(self.row().endswith(f"| `{HEAD[:7]}` | {label} |"))
                            self.assertIn(check.relative_time(DONE), self.row())
                            self.assertEqual(check.last_completed_review_details(self.api.status)["trigger"], label)
                            caveat = ("Integration with the current baseline has not been reviewed."
                                      if movement == "base" else "⚠️ This completed review doesn’t cover the current version."
                                      if result == "success" else "⚠️ These recorded findings are from a review of a previous version.")
                            self.assertGreater(self.visible().index(caveat), self.visible().index(HEADER))

    def test_repeat_neither_relabels_nor_backfills_legacy_event(self):
        for recorded in (True, False):
            with self.subTest(recorded=recorded):
                self.api.status = None
                self.start(TRIGGER_LABEL="PR opened for review",
                           DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                self.finish()
                if not recorded:
                    self.api.status["body"] = "\n".join(line for line in self.api.status["body"].splitlines()
                        if not line.startswith(check.STATUS_REVIEWED_TRIGGER_PREFIX))
                os.environ["TRIGGER_LABEL"] = "Draft marked ready"
                self.finish()
                self.assertTrue(self.row().endswith("| " + ("PR opened for review" if recorded else "Not recorded") + " |"))
                self.assertEqual(check.last_completed_review_details(self.api.status).get("trigger"),
                                 "PR opened for review" if recorded else None)
                self.finish("action_required")
                self.assertTrue(self.row().endswith("| " + ("PR opened for review" if recorded else "Not recorded") + " |"))
                self.assertIn("⚠️ **Findings**", self.row())

    def test_newer_attempt_prominent_while_history_keeps_its_original_event(self):
        for state in ("in_progress", "failure", "publication_incomplete"):
            with self.subTest(state=state):
                self.api.status = None
                self.start(GITHUB_RUN_ID=5, PRESENTATION_START=2, TRIGGER_LABEL="PR opened for review",
                           DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                self.finish()
                self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3, TRIGGER_LABEL="Draft marked ready",
                           DETAILS_URL="https://github.com/owner/repo/actions/runs/6")
                if state != "in_progress":
                    check.best_effort_status(HEAD, state, completed_at="2026-10-07T14:00:00Z")
                latest = "## 🔄 Claude Review in progress" if state == "in_progress" else "**Reason:**"
                self.assertLess(self.visible().index(latest), self.visible().index(HEADER))
                self.assertIn("✅ **Completed**", self.row())
                self.assertIn("**Last completed review**", self.visible())
                self.assertTrue(self.row().endswith("| PR opened for review |"))
                self.assertNotIn("14:00:00Z", self.row())
                self.finish()
                self.assertTrue(self.row().endswith("| Draft marked ready |"))

    def test_invalid_or_untrusted_trigger_provenance_is_not_displayed(self):
        self.start(TRIGGER_LABEL="PR opened for review",
                   DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
        self.finish()
        original = copy.deepcopy(self.api.status)
        trigger = next(line for line in original["body"].splitlines()
                       if line.startswith(check.STATUS_REVIEWED_TRIGGER_PREFIX))
        for mutation in ("missing", "duplicate", "invalid", "noncanonical", "generation",
                         "run", "duplicate_head", "untrusted"):
            with self.subTest(mutation=mutation):
                comment = copy.deepcopy(original)
                if mutation == "missing":
                    comment["body"] = comment["body"].replace(trigger, "")
                elif mutation == "duplicate":
                    comment["body"] += "\n" + trigger
                elif mutation in ("invalid", "noncanonical"):
                    value = "synchronize" if mutation == "invalid" else "PR opened for review"
                    comment["body"] = comment["body"].replace(trigger, check.STATUS_REVIEWED_TRIGGER_PREFIX + value + " -->")
                elif mutation in ("generation", "run"):
                    prefix = check.STATUS_REVIEWED_GENERATION_PREFIX if mutation == "generation" else check.STATUS_REVIEWED_RUN_URL_PREFIX
                    comment["body"] = "\n".join(line for line in comment["body"].splitlines() if not line.startswith(prefix))
                elif mutation == "duplicate_head":
                    comment["body"] += "\n" + check.STATUS_REVIEWED_HEAD_PREFIX + HEAD + " -->"
                else:
                    comment["user"]["type"] = "User"
                details = check.last_completed_review_details(comment)
                self.assertNotIn("trigger", details)
                body = check.status_comment_body(OTHER, "main", "stale", owner=check.status_owner(original),
                    last_review=check.last_completed_review(original), last_review_details=details)
                self.assertIn(f"| `{HEAD[:7]}` | Not recorded |", body)

    def test_trigger_must_match_accepted_request_kind_and_no_codex_markers(self):
        for label in (None, "synchronize", "Manual request"):
            with self.subTest(label=label):
                self.api.status = None
                self.start(TRIGGER_LABEL=label or "", DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                self.finish()
                self.assertTrue(self.row().endswith("| Not recorded |"))
                self.assertNotIn("codex", self.api.status["body"].lower())

    def test_history_survives_newer_request_of_different_kind(self):
        for prior_manual in (False, True):
            with self.subTest(prior_manual=prior_manual):
                self.api.status = None
                os.environ.update(TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic")
                os.environ.pop("TRIGGER_COMMENT_ID", None)
                if prior_manual:
                    self.target("issue_comment")
                label = "Manual request" if prior_manual else "PR opened for review"
                self.start(GITHUB_RUN_ID=5, PRESENTATION_START=2, TRIGGER_LABEL=label,
                           DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                self.finish()
                if prior_manual:
                    os.environ.update(TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic")
                    os.environ.pop("TRIGGER_COMMENT_ID", None)
                else:
                    self.target("pull_request_review_comment")
                self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3,
                           TRIGGER_LABEL="Draft marked ready" if prior_manual else "Manual request",
                           DETAILS_URL="https://github.com/owner/repo/actions/runs/6")
                self.assertEqual(check.status_owner(self.api.status)["run"], 6)
                self.assertEqual(check.status_owner(self.api.status)["kind"], "automatic" if prior_manual else "pull_request_review_comment")
                self.assertTrue(self.row().endswith("| " + label + " |"))
                self.assertEqual(check.last_completed_review_details(self.api.status)["trigger"], label)

    def test_emergency_fallback_preserves_original_event_for_helper(self):
        import json
        from test_claude_review_workflows import ToolingFallbackScriptTests, AUTOMATIC, MANUAL
        for workflow in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=workflow.name):
                self.api.status = None
                os.environ.update(TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic")
                os.environ.pop("TRIGGER_COMMENT_ID", None)
                owner = self.start(TRIGGER_LABEL="Draft marked ready",
                                   DETAILS_URL="https://github.com/owner/repo/actions/runs/5")
                self.finish()
                run, calls = ToolingFallbackScriptTests().run_fallback(workflow,
                    OWNER_FIXTURE=owner, REPO=owner["repo"], PR_NUMBER="7", STATUS_COMMENT_ID="55",
                    responses={"STATUS": [{"stdout": json.dumps(self.api.status)}],
                               "PR": [{"stdout": json.dumps(self.api.pr)}]})
                self.assertEqual(run.returncode, 1, run.stderr)
                repaired = next(c["body"]["body"] for c in calls if c["method"] == "PATCH"
                                and any("issues/comments/55" in arg for arg in c["args"]))
                self.api.status["body"] = repaired
                self.assertEqual(check.last_completed_review_details(self.api.status)["trigger"], "Draft marked ready")
                os.environ["TRIGGER_LABEL"] = "PR opened for review"
                check.best_effort_status(HEAD, "failure")
                self.assertTrue(self.row().endswith("| Draft marked ready |"))
                self.assertIn(check.relative_time(DONE), self.row())
