"""Offline historical posted-count regressions for repeated finalization."""

import copy
import json
import os
from unittest import mock

from test_claude_review_check import (
    ScriptTestCase, check, HEAD, OTHER, REPO, check_run, comment,
    evidence_of, history_pages, history_rule, comments_rule, patch_rule, ok, pages,
    presentation_owner, status_comment, status_list_rule, status_patch_rule, FAIL,
)
import test_presentation_ownership as ownership


class InlineFailureReplayTests(ScriptTestCase):
    def finalize_failure(self, previous=None, *, observed=(), status=None, snapshot="[]", other_runs=(),
                         history_failure=False, comment_failure=False, **env):
        self.log.unlink(missing_ok=True)
        if snapshot is None:
            self.before.unlink(missing_ok=True)
        else:
            self.before.write_text(snapshot)
        current = ({**check_run(99, previous["conclusion"]), "output": previous["output"]}
                   if previous else check_run(99, None, status="in_progress"))
        rules = [history_rule(FAIL if history_failure else ok(history_pages({"check_runs": [current, *other_runs]}))),
                 comments_rule(FAIL if comment_failure else ok(pages([comment(i) for i in observed]))),
                 patch_rule(ok("{}")), status_list_rule(ok(pages([status or status_comment(HEAD)]))),
                 status_patch_rule(ok("{}"))]
        if history_failure:
            rules.append({"method": "GET", "path": f"repos/{REPO}/check-runs/99",
                          "responses": [ok(json.dumps(current))]})
        result = self.run_script(["finalize"], rules, CHECK_RUN_ID="99", REVIEW_RESULT="success",
                                 ACTION_CONCLUSION="success", BEFORE_IDS_FILE=str(self.before),
                                 STATUS_COMMENTS_ENABLED="true", COMPLETION_VERIFIED="false",
                                 COMPLETION_REASON="errored_inline_tool_result", **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        writes = self.calls("PATCH")
        check_body = next(c["body"] for c in writes if c["path"].endswith("/check-runs/99"))
        status_body = next(c["body"]["body"] for c in writes if c["path"].endswith("/issues/comments/55"))
        return check_body, {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"}, "body": status_body}

    def assert_count(self, check_body, status, count):
        expected = check.inline_failure_explanation(count)
        self.assertIn(expected, check_body["output"]["summary"])
        self.assertIn(expected, status["body"])
        self.assertEqual(check_body["conclusion"], "failure")
        owner = check.status_owner(status)
        self.assertEqual(check.status_inline_failure(status, owner)["posted_count"], count)
        self.assertNotIn("manual-clean-completion", status["body"])
        return owner

    def test_same_attempt_replay_after_comment_deletion_retains_count_and_stale_receipt(self):
        first, status = self.finalize_failure(observed=[9])
        owner = self.assert_count(first, status, 1)
        for _ in range(2):
            replay, status = self.finalize_failure(first, status=status)
            self.assertEqual(self.assert_count(replay, status, 1), owner)
            self.assertEqual(evidence_of(replay)["claude_finding_comment_ids"], [9])
            self.assertIs(evidence_of(replay)["same_diff_findings_recorded"], True)
            first = replay

        # A later lifecycle refresh has no execution result of its own.
        api = ownership.PresentationAPI()
        api.status = status
        api.pr["head"]["sha"] = OTHER
        with mock.patch.dict(os.environ, {"REPO": REPO, "PR_NUMBER": "7", "BASE_REF": "main",
                                         "STATUS_COMMENTS_ENABLED": "true"}, clear=True), \
                mock.patch.object(check, "gh_api", side_effect=api.call):
            check.best_effort_status(OTHER, "stale")
        self.assertEqual(check.status_owner(api.status), owner)
        self.assertEqual(check.status_inline_failure(api.status, owner)["posted_count"], 1)
        self.assertIn("check_run_id=99", api.status["body"])

    def test_unknown_count_cannot_become_zero_on_replay(self):
        first, status = self.finalize_failure(snapshot="invalid")
        self.assert_count(first, status, None)
        replay, status = self.finalize_failure(first, status=status)
        self.assert_count(replay, status, None)

    def test_confirmed_zero_remains_zero_on_replay(self):
        first, status = self.finalize_failure()
        self.assert_count(first, status, 0)
        replay, status = self.finalize_failure(first, status=status)
        self.assert_count(replay, status, 0)

    def test_completed_legacy_or_mismatched_receipts_do_not_claim_zero(self):
        first, status = self.finalize_failure(observed=[9])
        evidence = evidence_of(first)
        mutations = [lambda e: e.pop("inline_failure", None),
                     lambda e: e.update(inline_failure={}),
                     lambda e: e["inline_failure"].update(generation="0" * 64),
                     lambda e: e["inline_failure"].update(check_run_id=100),
                     lambda e: e["inline_failure"].update(posted_count=True),
                     lambda e: e["inline_failure"].update(posted_count=-1),
                     lambda e: e["inline_failure"].update(posted_count=0),
                     lambda e: e.update(runtime_sha="e" * 40)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                old = copy.deepcopy(evidence)
                mutate(old)
                previous = copy.deepcopy(first)
                previous["output"]["text"] = check.evidence_text(old)
                replay, retained = self.finalize_failure(previous, status=status)
                self.assert_count(replay, retained, None)
                self.assertEqual(evidence_of(replay)["claude_finding_comment_ids"], [9])

    def test_other_attempt_findings_never_supply_the_current_attempt_count(self):
        first, status = self.finalize_failure(observed=[9])
        earlier = {**check_run(98, "failure"), "output": first["output"]}
        current, retained = self.finalize_failure(other_runs=[earlier])
        self.assert_count(current, retained, 0)
        self.assertEqual(evidence_of(current)["claude_finding_comment_ids"], [9])
        new_owner = presentation_owner(run=6, started=3)
        fresh_status = {"id": 55, "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                        "body": check.status_comment_body(HEAD, "main", "in_progress", owner=new_owner)}
        current, retained = self.finalize_failure(first, status=fresh_status,
                                                 PRESENTATION_OWNER=json.dumps(new_owner), GITHUB_RUN_ID="6")
        self.assertEqual(self.assert_count(current, retained, None), new_owner)

    def test_historical_count_survives_unavailable_current_discovery(self):
        first, status = self.finalize_failure(observed=[9])
        for changes in ({"history_failure": True}, {"comment_failure": True}, {"snapshot": "invalid"}):
            with self.subTest(changes=changes):
                replay, retained = self.finalize_failure(first, status=status, **changes)
                self.assert_count(replay, retained, 1)
                self.assertTrue(evidence_of(replay)["evidence_errors"])
                self.assertEqual(evidence_of(replay)["claude_finding_comment_ids"], [9])

    def test_newly_discovered_ids_leave_replayed_total_unknown(self):
        for first_ids, later_ids in (([], [9]), ([9], [10]), ([9], [9, 10])):
            with self.subTest(first_ids=first_ids, later_ids=later_ids):
                first, status = self.finalize_failure(observed=first_ids)
                replay, retained = self.finalize_failure(first, status=status, observed=later_ids)
                self.assert_count(replay, retained, None)
                self.assertEqual(evidence_of(replay)["claude_finding_comment_ids"], sorted(set(first_ids + later_ids)))
                self.assertNotIn("Claude posted", replay["output"]["summary"])
