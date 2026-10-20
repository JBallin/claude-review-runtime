"""Accepted trigger provenance survives stale presentation, never refresh inference."""
import copy
import json
import os
import unittest
from unittest import mock

from test_claude_review_check import check, HEAD, OTHER, presentation_owner
import test_presentation_ownership as ownership
from test_presentation_ownership import PresentationAPI


class AttemptTriggerTests(unittest.TestCase):
    def setUp(self):
        original_path = os.environ["PATH"]
        ownership.OwnershipTests.setUp(self)
        os.environ.update(PATH=original_path, DETAILS_URL="https://github.com/owner/repo/actions/runs/5")

    start = ownership.OwnershipTests.start
    target = ownership.OwnershipTests.target

    def test_acquisition_and_refresh_keep_exact_accepted_event_on_movement(self):
        for label, kind in (("PR opened for review", "automatic"),
                            ("Draft marked ready", "automatic"),
                            ("Manual request", "issue_comment")):
            for moved in ("head", "base", "ref"):
                for at_start in (True, False):
                    with self.subTest(label=label, moved=moved, at_start=at_start):
                        self.api = PresentationAPI()
                        self.gh_mock.side_effect = self.api.call
                        os.environ.update(self.env, TRIGGER_LABEL=label)
                        os.environ.pop('TRIGGER_COMMENT_ID', None)
                        if kind != 'automatic':
                            self.target(kind)
                        def move():
                            if moved == 'head': self.api.pr['head']['sha'] = OTHER
                            elif moved == 'base': self.api.base_tip = OTHER
                            else: self.api.pr['base']['ref'] = 'release'
                        if at_start: move()
                        owner = self.start()
                        if not at_start: move()
                        for refresh in ('Draft marked ready', 'Manual request', ''):
                            os.environ.update(TRIGGER_LABEL=refresh,
                                HEAD_SHA=self.api.pr['head']['sha'], BASE_REF=self.api.pr['base']['ref'])
                            check.cmd_stale()
                            self.assertEqual(check.status_state(self.api.status), 'stale')
                            self.assertEqual(check.status_owner(self.api.status), owner)
                            self.assertEqual(check.status_attempt_trigger(self.api.status, owner), label)
                            row = next(x for x in self.api.status['body'].splitlines() if x.startswith('| ') and x.endswith('| ' + label + ' |'))
                            self.assertIn(check.commit_link(owner['head']), row)
                            self.assertNotIn('Not recorded', row)

    def test_history_is_distinct_and_invalid_receipts_never_borrow_refresh_labels(self):
        os.environ['TRIGGER_LABEL'] = 'PR opened for review'
        self.start()
        check.best_effort_status(HEAD, 'success', completed_at='2026-10-07T13:09:19Z')
        os.environ['TRIGGER_LABEL'] = 'Draft marked ready'
        owner = self.start(GITHUB_RUN_ID='6', PRESENTATION_START='3')
        self.api.pr['head']['sha'] = OTHER
        os.environ['HEAD_SHA'] = OTHER
        check.cmd_stale()
        self.assertIn('**Review trigger:** PR opened for review', self.api.status['body'])
        self.assertIn('| Draft marked ready |', self.api.status['body'])
        valid = copy.deepcopy(self.api.status)
        marker = check.attempt_trigger_marker(owner, 'Draft marked ready')
        for mutation in ('missing', 'malformed', 'mismatch', 'duplicate', 'broken_duplicate', 'wrong_kind', 'noncanonical', 'author'):
            with self.subTest(mutation=mutation):
                c = copy.deepcopy(valid)
                replacement = {'missing':'', 'malformed':check.ATTEMPT_TRIGGER_PREFIX+'%7B -->',
                    'mismatch':check.attempt_trigger_marker(presentation_owner(run=7), 'Draft marked ready'),
                    'duplicate':marker+'\n'+marker,
                    'broken_duplicate':marker+'\n'+check.ATTEMPT_TRIGGER_PREFIX+'broken',
                    'wrong_kind':check.attempt_trigger_marker(owner, 'Manual request'),
                    'noncanonical':marker.replace('%5B', '%5b')}.get(mutation, marker)
                c['body'] = c['body'].replace(marker, replacement)
                if mutation == 'author': c['user']['type'] = 'User'
                self.assertIsNone(check.status_attempt_trigger(c, owner))
                if mutation != 'author':
                    self.api.status = c
                    os.environ['TRIGGER_LABEL'] = 'PR opened for review'
                    check.cmd_stale()
                    self.assertIn('| Not recorded |', self.api.status['body'])
                    self.assertEqual(check.status_owner(self.api.status), owner)
                    self.assertEqual(check.last_completed_review(self.api.status), (HEAD, 'main', 'success'))
                    self.assertIsNone(check.status_attempt_trigger(self.api.status, owner))

    def test_repeat_completion_does_not_backfill_attempt_receipt_from_review_history(self):
        os.environ['TRIGGER_LABEL'] = 'PR opened for review'
        owner = self.start()
        check.best_effort_status(HEAD, 'success', completed_at='2026-10-07T13:09:19Z')
        marker = check.attempt_trigger_marker(owner, 'PR opened for review')
        self.api.status['body'] = self.api.status['body'].replace(marker + '\n', '')
        self.assertEqual(check.last_completed_review_details(self.api.status)['trigger'], 'PR opened for review')
        os.environ['TRIGGER_LABEL'] = 'Draft marked ready'
        check.best_effort_status(HEAD, 'success')
        self.assertNotIn(check.ATTEMPT_TRIGGER_PREFIX, self.api.status['body'])
        self.assertIn('| PR opened for review |', self.api.status['body'])
        self.api.pr['head']['sha'] = OTHER
        os.environ['HEAD_SHA'] = OTHER
        check.cmd_stale()
        self.assertNotIn(check.ATTEMPT_TRIGGER_PREFIX, self.api.status['body'])
        self.assertIsNone(check.status_attempt_trigger(self.api.status, owner))
        self.assertEqual(check.status_owner(self.api.status), owner)
        self.assertEqual(check.last_completed_review(self.api.status), (HEAD, 'main', 'success'))

    def test_emergency_round_trip_retains_only_verified_attempt_receipt(self):
        from test_claude_review_workflows import ToolingFallbackScriptTests, AUTOMATIC, MANUAL, REPO as workflow_repo, pr_identity
        for workflow in (AUTOMATIC, MANUAL):
            for valid in (True, False):
                with self.subTest(workflow=workflow.name, valid=valid):
                    owner = presentation_owner(repo=workflow_repo)
                    body = check.status_comment_body(HEAD, 'main', 'in_progress', owner=owner,
                        trigger_label='Draft marked ready')
                    if not valid:
                        body = body.replace(check.attempt_trigger_marker(owner, 'Draft marked ready'),
                            check.attempt_trigger_marker(presentation_owner(repo=workflow_repo, run=9), 'Draft marked ready'))
                    status = {'id':55, 'user':{'login':check.STATUS_AUTHOR,'type':'Bot'},'body':body}
                    run, calls = ToolingFallbackScriptTests().run_fallback(workflow, OWNER_FIXTURE=owner,
                        STATUS_COMMENT_ID='55', PR_NUMBER='7', TRIGGER_LABEL='PR opened for review',
                        responses={'STATUS':[{'stdout':json.dumps(status)}], 'PR':[{'stdout':pr_identity(OTHER)}]})
                    self.assertEqual(run.returncode, 1, run.stderr)
                    repaired = next(c['body']['body'] for c in calls if c['method']=='PATCH'
                        and any('issues/comments/55' in a for a in c['args']))
                    record = {**status, 'body':repaired}
                    self.assertEqual(check.status_owner(record), owner)
                    self.assertEqual(check.status_attempt_trigger(record, owner), 'Draft marked ready' if valid else None)
                    self.assertIn('**Trigger:** ' + ('Draft marked ready' if valid else 'Not recorded'), repaired)
                    saved = []
                    def publish(args, body=None):
                        if "PATCH" in args:
                            saved.append(body["body"])
                            return "{}"
                        return "[]"
                    with mock.patch.dict(os.environ, REPO=workflow_repo, HEAD_SHA=OTHER, BASE_REF='release'), \
                            mock.patch.object(check, 'find_status_comment', return_value=record), \
                            mock.patch.object(check, 'live_pr_identity', return_value=(OTHER, 'release', OTHER)), \
                            mock.patch.object(check, 'gh_api', side_effect=publish):
                        check.best_effort_status(OTHER, 'stale')
                    self.assertEqual(len(saved), 1)
                    refreshed = saved[0]
                    self.assertIn('| ' + ('Draft marked ready' if valid else 'Not recorded') + ' |', refreshed)
                    self.assertEqual(check.status_owner({'body':refreshed}), owner)
                    self.assertEqual(check.status_attempt_trigger({**record,'body':refreshed}, owner),
                                     'Draft marked ready' if valid else None)
                    # A refresh label cannot replace the accepted event preserved by emergency tooling.
                    self.assertNotIn(check.attempt_trigger_marker(owner, 'PR opened for review'), repaired)
