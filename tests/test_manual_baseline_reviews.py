"""Offline captured-patch completion contracts for differing manual baselines."""
import copy
import json
import os
import unittest

from test_claude_review_check import check, HEAD, OTHER, NEWER, BASE_TIP, MERGE_BASE, REPO
import test_presentation_ownership as ownership


DONE = "2026-10-07T13:09:19Z"
LATER = "2026-10-07T14:00:00Z"


class ManualAPI(ownership.PresentationAPI):
    """Keep completion notices distinct from the one shared status comment."""

    def __init__(self):
        super().__init__()
        self.notices = []
        self.lose_notice_response = False

    def call(self, args, body=None):
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        path = next(item for item in args if item not in
                    ("--method", "--paginate", "POST", "PATCH", "DELETE", "--input", "-"))
        path = path.split("?")[0]
        if (method == "POST" and path == f"repos/{REPO}/issues/7/comments"
                and check.STATUS_MARKER not in body["body"]):
            self.calls.append((method, path, copy.deepcopy(body)))
            if self.on_call:
                self.on_call(method, path)
            notice = {"id": 88 + len(self.notices),
                      "user": {"login": check.STATUS_AUTHOR, "type": "Bot"},
                      "body": body["body"]}
            self.notices.append(notice)
            self.comment_pages.append(notice)
            if self.lose_notice_response:
                raise check.GhError("response lost after completion notice persisted")
            return json.dumps(notice)
        return super().call(args, body)


class ManualBaselineTests(unittest.TestCase):
    def setUp(self):
        ownership.OwnershipTests.setUp(self)
        self.reset_manual()

    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target
    owned = ownership.OwnershipTests.owned

    def reset_manual(self, kind="issue_comment"):
        self.api = ManualAPI()
        self.gh_mock.side_effect = self.api.call
        self.output.clear()
        os.environ.update(self.env)
        os.environ.update(MERGE_BASE_SHA=MERGE_BASE, MANUAL_COMPLETION_ENABLED="true",
                          DETAILS_URL=f"https://github.com/{REPO}/actions/runs/5")
        os.environ.pop("PRESENTATION_OWNER", None)
        self.trigger = self.target(kind)

    def finish(self, state="success", available=True, when=DONE):
        return check.best_effort_status(HEAD, state, check_available=available,
                                        completed_at=when)

    def notice(self):
        check.best_effort_manual_completion(HEAD)

    def positive_posts(self):
        return [(method, path, body) for method, path, body in self.api.calls
                if method == "POST" and (body.get("content") == "+1"
                   or ("body" in body and check.STATUS_MARKER not in body["body"]))]

    def assert_qualified_completion(self, owner, result="success"):
        self.assertEqual(check.status_owner(self.api.status), owner)
        self.assertEqual(check.status_state(self.api.status), "stale")
        self.assertFalse(check.status_owner_running(self.api.status))
        self.assertEqual(check.last_completed_review(self.api.status), (HEAD, "main", result))
        details = check.last_completed_review_details(self.api.status)
        self.assertEqual(details["base_sha"], BASE_TIP)
        self.assertEqual(details["generation"], owner["generation"])
        self.assertEqual(details["completed_at"], DONE)
        self.assertIn(check.commit_link(BASE_TIP), self.api.status["body"])
        self.assertIn(check.commit_link(self.api.base_tip), self.api.status["body"])
        self.assertIn(check.TARGET_TIP_WARNING, self.api.status["body"])
        self.assertNotIn("Last completed review", self.api.status["body"])

    def test_clean_manual_completion_with_tip_and_projection_already_different(self):
        for kind in ("issue_comment", "pull_request_review_comment"):
            with self.subTest(kind=kind):
                self.reset_manual(kind)
                self.api.base_tip = OTHER
                self.api.pr["base"]["sha"] = NEWER
                owner = self.start()
                self.assertIsNotNone(owner)
                self.assertEqual(owner["base"], BASE_TIP)
                self.assertEqual(self.owned(self.opening), ["eyes"])
                self.assertEqual(self.owned(self.trigger), ["eyes"])
                self.finish()
                self.assert_qualified_completion(owner)
                self.assertEqual(self.owned(self.opening), ["+1"])
                self.assertEqual(self.owned(self.trigger), ["+1"])
                self.notice()
                self.assertEqual(len(self.api.notices), 1)
                body = self.api.notices[0]["body"]
                self.assertIn(BASE_TIP, body)
                self.assertIn(HEAD, body)
                self.assertIn(MERGE_BASE, body)
                self.assertEqual(self.api.base_tip, OTHER)
                self.assertIn(check.TARGET_TIP_WARNING, self.api.status["body"].split("<details>")[0])
                self.assertNotIn(check.TARGET_TIP_WARNING, body)
                visible, details = body.split("<details>", 1)
                self.assertNotIn("The observed target differed", visible)
                self.assertIn("The observed target differed from the reviewed baseline.", details)
                self.assertIn("Claude Review: No findings. 🎉", body)
                self.assertIn("**Reviewed commit:** " + check.commit_link(HEAD, REPO), body)
                self.assertIn("**Observed target commit:** " + check.commit_link(OTHER, REPO), body)
                self.assertNotIn("**Current target commit:**", body)
                for rendered in (self.api.status["body"], body):
                    self.assertNotIn("has changed since this review", rendered)
                self.assertNotIn("approved", body.lower())

    def test_refresh_and_repeat_leave_historical_receipts_unchanged(self):
        for kind in ("issue_comment", "pull_request_review_comment"):
            for receipt_format in ("current", "without_note", "legacy_warning"):
                with self.subTest(kind=kind, receipt_format=receipt_format):
                    self.reset_manual(kind)
                    owner = self.start()
                    self.api.base_tip = OTHER
                    self.finish()
                    self.notice()
                    if receipt_format != "current":
                        # Earlier receipts did not have the historical Details note.
                        self.api.notices[0]["body"] = self.api.notices[0]["body"].replace(
                            "\n\nThe observed target differed from the reviewed baseline.", "")
                    if receipt_format == "legacy_warning":
                        # Simulate an already-published receipt from the original renderer.
                        self.api.notices[0]["body"] = self.api.notices[0]["body"].replace(
                            "<details>", check.TARGET_TIP_WARNING + "\n\n<details>", 1)
                    original = copy.deepcopy(self.api.notices)
                    self.api.base_tip = NEWER
                    self.api.calls.clear()
                    check.cmd_stale()
                    self.assert_qualified_completion(owner)
                    self.finish(when=LATER)
                    self.notice()
                    self.assertEqual(self.api.notices, original)
                    self.assertFalse(any(method == "POST" and path.endswith("/issues/7/comments")
                                         for method, path, _ in self.api.calls))
                    self.assertFalse(any(method == "PATCH" and path.endswith("/comments/88")
                                         for method, path, _ in self.api.calls))

    def test_projection_catching_up_during_direct_lookup_preserves_captured_patch(self):
        owner = self.start()
        self.api.base_tip = OTHER
        def catch_up(method, path):
            if "/git/ref/heads/" in path:
                self.api.pr["base"]["sha"] = OTHER
        self.api.on_call = catch_up
        self.finish()
        self.assert_qualified_completion(owner)
        self.assertEqual(self.owned(self.opening), ["+1"])
        self.assertEqual(self.owned(self.trigger), ["+1"])

    def test_only_clean_available_review_gains_positive_manual_signals(self):
        for state, available in (("action_required", True), ("failure", True),
                                 ("publication_incomplete", True), ("success", False)):
            with self.subTest(state=state, available=available):
                self.reset_manual()
                owner = self.start()
                self.api.base_tip = OTHER
                self.finish(state, available)
                self.assertEqual(self.owned(self.opening), [])
                self.assertEqual(self.owned(self.trigger), [])
                self.notice()
                self.assertFalse(self.api.notices)
                if state == "action_required":
                    self.assert_qualified_completion(owner, state)
                    self.assertIn("Findings", self.api.status["body"])
                    self.assertNotIn("reviewed clean", self.api.status["body"])

    def test_receipt_baseline_note_is_conditional_and_details_only(self):
        note = "The observed target differed from the reviewed baseline."
        marker = check.completion_marker(REPO, 7, HEAD, "main", BASE_TIP, MERGE_BASE)
        for observed in (None, "", BASE_TIP, OTHER, NEWER, MERGE_BASE):
            with self.subTest(observed=observed):
                body = check.completion_comment_body(
                    REPO, HEAD, "main", BASE_TIP, marker, os.environ["DETAILS_URL"],
                    observed_base_sha=observed)
                visible, details = body.split("<details>", 1)
                self.assertEqual(visible, marker + "\nClaude Review: No findings. 🎉\n\n"
                                 + "**Reviewed commit:** " + check.commit_link(HEAD, REPO) + "\n\n")
                self.assertNotIn(check.TARGET_TIP_WARNING, body)
                self.assertNotIn("**Current target commit:**", body)
                self.assertIn("**[Reviewed](" + os.environ["DETAILS_URL"] + ") against:** "
                              + check.commit_link(BASE_TIP, REPO), details)
                if observed and observed != BASE_TIP:
                    self.assertEqual(body.count(note), 1)
                    self.assertIn("**Observed target commit:** " + check.commit_link(observed, REPO)
                                  + " on `main`\n\n" + note + "\n\n</details>", details)
                else:
                    self.assertNotIn(note, body)
                    self.assertNotIn("**Observed target commit:**", body)

    def test_matching_baseline_notice_links_immutable_patch_without_generic_qualification(self):
        self.start()
        self.finish()
        self.notice()
        self.assertEqual(check.status_state(self.api.status), "success")
        self.assertEqual(len(self.api.notices), 1)
        self.assertNotIn("integration", self.api.notices[0]["body"].lower())
        self.assertNotIn("merge approval", self.api.notices[0]["body"].lower())
        self.assertIn(BASE_TIP, self.api.notices[0]["body"])

    def test_manual_receipt_distinguishes_runtime_from_reviewed_patch_and_baseline(self):
        runtime_sha = "f" * 40
        for kind in ("issue_comment", "pull_request_review_comment"):
            with self.subTest(kind=kind):
                self.reset_manual(kind)
                os.environ.update(RUNTIME_REPOSITORY="review/runtime", RUNTIME_SHA=runtime_sha,
                                  RUNTIME_WORKFLOW_PATH=".github/workflows/claude.yml")
                self.start()
                self.finish()
                self.notice()
                body = self.api.notices[0]["body"]
                visible, details = body.split("<details>", 1)
                self.assertNotIn("Runtime used", visible)
                self.assertIn(check.commit_link(HEAD, REPO), visible)
                self.assertIn(check.commit_link(BASE_TIP, REPO), details)
                self.assertEqual(details.count("**Runtime used:**"), 1)
                self.assertIn("**Runtime used:** [`review/runtime@fffffff`]"
                              f"(https://github.com/review/runtime/blob/{runtime_sha}/.github/workflows/claude.yml)",
                              details)
                self.assertNotIn("current main", body.lower())
                self.assertIn(check.completion_marker(REPO, 7, HEAD, "main", BASE_TIP, MERGE_BASE), body)
                identity = check.review_identity(HEAD)
                self.assertEqual(identity["runtime_sha"], runtime_sha)
                self.assertEqual(identity["reviewed_sha"], HEAD)

    def test_missing_or_unsafe_runtime_provenance_does_not_invent_a_runtime_link(self):
        valid = {"RUNTIME_REPOSITORY": "review/runtime", "RUNTIME_SHA": "f" * 40,
                 "RUNTIME_WORKFLOW_PATH": ".github/workflows/claude.yml"}
        marker = check.completion_marker(REPO, 7, HEAD, "main", BASE_TIP, MERGE_BASE)
        for key, value in (("RUNTIME_REPOSITORY", ""), ("RUNTIME_SHA", ""),
                           ("RUNTIME_WORKFLOW_PATH", ""), ("RUNTIME_REPOSITORY", "evil/repo](https://bad.invalid)"),
                           ("RUNTIME_SHA", "main"), ("RUNTIME_WORKFLOW_PATH", "../claude.yml")):
            with self.subTest(key=key, value=value):
                os.environ.update(valid)
                os.environ[key] = value
                body = check.completion_comment_body(REPO, HEAD, "main", BASE_TIP, marker,
                                                     os.environ["DETAILS_URL"])
                self.assertNotIn("**Runtime used:**", body)
                self.assertIn(check.commit_link(HEAD, REPO), body)

    def test_later_runtime_does_not_rewrite_or_duplicate_historical_manual_receipt(self):
        os.environ.update(RUNTIME_REPOSITORY="review/runtime", RUNTIME_SHA="f" * 40,
                          RUNTIME_WORKFLOW_PATH=".github/workflows/claude.yml")
        self.start()
        self.finish()
        self.notice()
        original = copy.deepcopy(self.api.notices)
        self.assertIn("review/runtime@fffffff", original[0]["body"])
        os.environ["RUNTIME_SHA"] = NEWER
        self.api.calls.clear()
        self.notice()
        self.assertEqual(self.api.notices, original)
        self.assertFalse(any(method in ("POST", "PATCH") and "comments" in path
                             for method, path, _ in self.api.calls))

    def test_selection_survives_standing_status_receipt_and_stale_refresh(self):
        for model, expected in (
                ('claude-opus-5-5', '**Model:** Opus · **Effort:** High'),
                ('claude-sonnet-4-6', 'primary model mismatch (Sonnet 4.6 observed)'),
                (None, 'primary model evidence missing')):
            with self.subTest(model=model):
                self.reset_manual()
                os.environ['TRIGGER_COMMENT'] = '/claude-review opus high'
                owner = self.start()
                self.assertIn('**Model:** Opus · **Effort:** High', self.api.status['body'])
                record = check.selection_record(owner)
                if model:
                    record.update(models=sorted(['claude-haiku-4-5', model]),
                                  model_evidence='mixed', execution_attempt=1,
                                  primary_model_evidence={'initialization': model,
                                                          'assistant': [model], 'attempt': 1})
                os.environ['SELECTION_EVIDENCE'] = json.dumps(record)
                self.finish()
                self.notice()
                self.assertEqual(len(self.api.notices), 1)
                for body in (self.api.status['body'], self.api.notices[0]['body']):
                    main, details = body.split('<details>', 1)
                    self.assertIn(expected, details if model == 'claude-opus-5-5' else main)
                    self.assertIn('**Effort:** High', details)
                    self.assertNotIn('**Effort:**', main)
                    self.assertNotIn('Haiku', body.split('<details>')[0])
                    self.assertNotIn('**Observed effort:**', body)
                receipt = self.api.notices[0]['body'].split('<details>')[0]
                self.assertIn('**Reviewed commit:** ' + check.commit_link(HEAD, REPO), receipt)
                for notice in check.selection_notice(record):
                    self.assertIn(notice, receipt)
                os.environ.pop('TRIGGER_COMMENT')
                os.environ.pop('SELECTION_EVIDENCE')
                self.api.base_tip = OTHER
                check.cmd_stale()
                self.assertIn(expected, self.api.status['body'])
                self.assertEqual(check.current_selection(owner, self.api.status), record)

    def test_completed_settings_follow_captured_patch_when_head_or_ref_moves_before_finish(self):
        for movement in ("head", "ref"):
            for state in ("success", "action_required"):
                with self.subTest(movement=movement, state=state):
                    self.reset_manual()
                    os.environ['TRIGGER_COMMENT'] = '/claude-review opus high'
                    owner = self.start()
                    record = check.selection_record(owner)
                    record.update(models=['claude-opus-5-5'], model_evidence='observed', execution_attempt=1,
                                  primary_model_evidence={'initialization': 'claude-opus-5-5',
                                                          'assistant': ['claude-opus-5-5'], 'attempt': 1})
                    os.environ['SELECTION_EVIDENCE'] = json.dumps(record)
                    if movement == "head":
                        self.api.pr['head']['sha'] = OTHER
                    else:
                        self.api.pr['base']['ref'] = 'release'
                    self.finish(state)
                    self.assertEqual(check.status_state(self.api.status), 'stale')
                    details = self.api.status['body'].split('<details>', 1)[1]
                    self.assertIn('**Model:** Opus · **Effort:** High', details)
                    self.assertEqual(check.last_completed_review_details(self.api.status)['selection'], record)
                    os.environ.pop('SELECTION_EVIDENCE')

    def test_status_refresh_cannot_recreate_removed_clean_signals(self):
        owner = self.start()
        self.finish()
        details = check.last_completed_review_details(self.api.status)
        self.api.base_tip = OTHER
        self.api.reactions.clear()
        for _ in range(2):
            self.api.calls.clear()
            check.cmd_stale()
            self.assert_qualified_completion(owner)
            self.assertEqual(check.last_completed_review_details(self.api.status), details)
            self.assertFalse(self.positive_posts())
            self.assertEqual(self.owned(self.opening) + self.owned(self.trigger), [])

    def test_trusted_repeat_can_recover_missing_reactions_without_redating_completion(self):
        owner = self.start()
        self.api.base_tip = OTHER
        self.finish()
        self.api.reactions.clear()
        self.finish(when=LATER)
        self.assert_qualified_completion(owner)
        self.assertEqual(self.owned(self.opening), ["+1"])
        self.assertEqual(self.owned(self.trigger), ["+1"])
        # A later failure remains adverse evidence even though the old clean
        # completion metadata is retained for historical display.
        self.finish("failure")
        self.api.calls.clear()
        self.notice()
        self.assertFalse(self.api.notices)
        self.assertFalse(self.positive_posts())

    def test_recovery_of_failed_publication_retains_captured_baseline(self):
        for failed_state in ("failure", "publication_incomplete"):
            with self.subTest(failed_state=failed_state):
                self.reset_manual()
                owner = self.start()
                self.api.base_tip = OTHER
                self.finish(failed_state)
                self.assertFalse(check.completed_review_for_owner(self.api.status, owner))
                self.finish()
                self.assert_qualified_completion(owner)
                self.assertEqual(self.owned(self.opening), ["+1"])
                self.assertEqual(self.owned(self.trigger), ["+1"])
                self.notice()
                self.assertEqual(len(self.api.notices), 1)

    def test_legacy_baseline_reason_preserves_completed_provenance_on_refresh(self):
        owner = self.start()
        self.api.base_tip = OTHER
        self.finish()
        details = check.last_completed_review_details(self.api.status)
        self.api.status["body"] = self.api.status["body"].replace(
            check.STATUS_REASONS["base_advanced"], check.LEGACY_BASE_ADVANCED_REASON)
        self.assertTrue(check.completed_review_for_owner(self.api.status, owner))
        self.api.reactions.clear()
        check.cmd_stale()
        self.assert_qualified_completion(owner)
        self.assertEqual(check.last_completed_review_details(self.api.status), details)
        self.assertFalse(self.owned(self.opening) + self.owned(self.trigger))

    def test_changed_head_or_ref_cannot_create_clean_reactions_or_notice(self):
        for changed in ("head", "ref"):
            with self.subTest(changed=changed):
                self.reset_manual()
                self.start()
                self.api.base_tip = OTHER
                if changed == "head":
                    self.api.pr["head"]["sha"] = NEWER
                else:
                    self.api.pr["base"]["ref"] = "release"
                self.api.calls.clear()
                self.finish()
                self.notice()
                self.assertFalse(self.positive_posts())
                self.assertEqual(self.owned(self.opening), [])
                self.assertEqual(self.owned(self.trigger), [])

    def test_unavailable_or_malformed_authority_cannot_create_positive_signals(self):
        for fault in ("unavailable", "bad_sha", "foreign_repo", "bad_projection"):
            with self.subTest(fault=fault):
                self.reset_manual()
                self.start()
                if fault == "unavailable":
                    self.api.faults[("GET", f"repos/{REPO}/git/ref/heads/main")] = "fail"
                elif fault == "bad_sha":
                    self.api.base_tip = "short"
                elif fault == "foreign_repo":
                    self.api.pr["base"]["repo"]["full_name"] = "other/repo"
                else:
                    self.api.pr["base"]["sha"] = "short"
                self.api.calls.clear()
                self.finish()
                self.notice()
                self.assertFalse(self.positive_posts())

    def test_head_change_after_opening_reaction_blocks_trigger_and_notice(self):
        self.start()
        self.api.base_tip = OTHER
        def move_after_opening(method, path):
            if method == "POST" and path == self.opening:
                self.api.pr["head"]["sha"] = NEWER
        self.api.on_call = move_after_opening
        self.finish()
        self.notice()
        self.assertEqual(self.owned(self.trigger), [])
        self.assertFalse(self.api.notices)

    def test_valid_tip_movement_between_publication_writes_does_not_replace_patch(self):
        for after in ("status", "opening"):
            with self.subTest(after=after):
                self.reset_manual()
                owner = self.start()
                self.api.base_tip = OTHER
                def move_tip(method, path):
                    if ((after == "status" and method == "PATCH" and path.endswith("/comments/55"))
                            or (after == "opening" and method == "POST" and path == self.opening)):
                        self.api.base_tip = NEWER
                self.api.on_call = move_tip
                self.finish()
                self.assertEqual(check.last_completed_review_details(self.api.status)["base_sha"], owner["base"])
                self.assertEqual(self.owned(self.opening), ["+1"])
                self.assertEqual(self.owned(self.trigger), ["+1"])
                self.notice()
                self.assertEqual(len(self.api.notices), 1)
                self.assertIn(BASE_TIP, self.api.notices[0]["body"])

    def test_automatic_verified_historical_completion_retains_existing_behavior(self):
        os.environ.update(TRIGGER_KIND="automatic", PR_REACTION_MODE="automatic",
                          MANUAL_COMPLETION_ENABLED="false")
        os.environ.pop("TRIGGER_COMMENT_ID", None)
        self.start()
        self.api.base_tip = OTHER
        self.finish()
        # Existing trusted historical automatic completions retain their clean
        # signal. This change does not introduce automatic completion notices.
        self.assertEqual(self.owned(self.opening), ["+1"])
        self.notice()
        self.assertFalse(self.api.notices)

    def test_notice_requires_exact_executing_receipt_and_persisted_clean_owner(self):
        for fault in ("disabled", "missing_receipt", "wrong_attempt", "wrong_run", "missing_status",
                      "unverified_completion", "newer_owner"):
            with self.subTest(fault=fault):
                self.reset_manual()
                self.start()
                self.finish()
                if fault == "disabled":
                    os.environ["MANUAL_COMPLETION_ENABLED"] = "false"
                elif fault == "missing_receipt":
                    os.environ.pop("PRESENTATION_OWNER", None)
                elif fault == "wrong_attempt":
                    os.environ["GITHUB_RUN_ATTEMPT"] = "2"
                elif fault == "wrong_run":
                    os.environ["GITHUB_RUN_ID"] = "6"
                elif fault == "missing_status":
                    self.api.status = None
                elif fault == "unverified_completion":
                    self.api.status["body"] = "\n".join(line for line in self.api.status["body"].splitlines()
                        if not line.startswith(check.STATUS_REVIEWED_GENERATION_PREFIX))
                else:
                    receipt = os.environ["PRESENTATION_OWNER"]
                    self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3)
                    os.environ.update(PRESENTATION_OWNER=receipt, GITHUB_RUN_ID="5", PRESENTATION_START="2")
                self.api.calls.clear()
                self.notice()
                self.assertFalse(self.api.notices)

    def test_notice_rechecks_head_ref_and_owner_after_discovery(self):
        for change in ("head", "ref", "owner"):
            with self.subTest(change=change):
                self.reset_manual()
                self.start()
                self.finish()
                replacement = copy.deepcopy(self.api.status)
                if change == "owner":
                    receipt = os.environ["PRESENTATION_OWNER"]
                    self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3)
                    replacement, self.api.status = self.api.status, replacement
                    os.environ.update(PRESENTATION_OWNER=receipt, GITHUB_RUN_ID="5", PRESENTATION_START="2")
                def replace(method, path):
                    if method == "GET" and path == f"repos/{REPO}/git/ref/heads/main":
                        if change == "head":
                            self.api.pr["head"]["sha"] = NEWER
                        elif change == "ref":
                            self.api.pr["base"]["ref"] = "release"
                        else:
                            self.api.status = replacement
                self.api.on_call = replace
                self.notice()
                self.assertFalse(self.api.notices)

    def test_lost_notice_response_is_not_retried_and_same_identity_deduplicates(self):
        self.start()
        self.api.base_tip = OTHER
        self.finish()
        self.api.lose_notice_response = True
        self.notice()
        self.assertEqual(len(self.api.notices), 1)
        self.api.base_tip = NEWER
        self.api.pr["base"]["sha"] = OTHER
        self.notice()
        self.assertEqual(len(self.api.notices), 1)
        posts = [call for call in self.api.calls if call[0] == "POST"
                 and call[1].endswith("/comments") and check.STATUS_MARKER not in call[2]["body"]]
        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()
