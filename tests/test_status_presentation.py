"""Offline information hierarchy and terminal-reason compatibility."""
import os
import unittest

from test_claude_review_check import check, HEAD, OTHER
import test_presentation_ownership as ownership


class StatusPresentationTests(unittest.TestCase):
    def setUp(self):
        ownership.OwnershipTests.setUp(self)
        os.environ["DETAILS_URL"] = "https://github.com/owner/repo/actions/runs/5"
    start = ownership.OwnershipTests.start

    def visible(self, body):
        return body.split('<details>')[0]

    def test_changed_head_leads_with_history_and_visible_coverage(self):
        for result in ('success', 'action_required'):
            with self.subTest(result=result):
                self.api.status = None
                self.api.pr['head']['sha'] = HEAD
                self.start()
                check.best_effort_status(HEAD, result, completed_at='2026-10-07T13:09:19Z')
                self.api.pr['head']['sha'] = OTHER
                check.best_effort_status(OTHER, 'stale')
                body = self.api.status['body']
                visible = self.visible(body)
                self.assertIn(f'**Last reviewed:** `{HEAD[:7]}`', visible)
                expected = 'Last Claude review: no findings' if result == 'success' else 'Last Claude review: findings'
                heading = next(line for line in visible.splitlines() if line.startswith('### '))
                self.assertEqual(heading, '### ' + expected)
                self.assertIn('This review doesn’t cover the current version.', visible)
                self.assertNotIn('Claude Review stale', visible)
                self.assertNotIn('coverage', visible)
                self.assertNotIn('head and base', visible)
                self.assertNotIn('Request a new review', body)
                for label in ('Current commit', 'Captured commit', 'Captured baseline', 'Started'):
                    self.assertNotIn(f'**{label}:**', visible)
                self.assertEqual(check.status_head(self.api.status), OTHER)
                self.assertEqual(check.status_state(self.api.status), 'stale')
                self.assertEqual(check.last_completed_review(self.api.status), (HEAD, 'main', result))
                if result == 'action_required':
                    self.assertIn('inline review threads', visible)
                    self.assertNotIn('Claude Review passed', visible)

    def test_latest_failed_or_running_attempt_precedes_old_clean_result(self):
        for state in ('in_progress', 'failure', 'publication_incomplete'):
            with self.subTest(state=state):
                self.api.status = None
                self.api.pr['head']['sha'] = HEAD
                self.start(GITHUB_RUN_ID=5, PRESENTATION_START=2)
                check.best_effort_status(HEAD, 'success', completed_at='2026-10-07T13:09:19Z')
                self.start(GITHUB_RUN_ID=6, PRESENTATION_START=3)
                if state != 'in_progress':
                    check.best_effort_status(HEAD, state)
                self.api.pr['head']['sha'] = OTHER
                check.best_effort_status(OTHER, 'stale')
                body = self.api.status['body']
                visible = self.visible(body)
                latest = 'latest review attempt is still in progress' if state == 'in_progress' else '**Reason:**'
                self.assertLess(visible.index(latest), visible.index('**Last reviewed:**'))
                self.assertNotIn('Claude Review passed', visible)
                self.assertNotIn('### Last Claude review:', visible)
                if state != 'in_progress':
                    reason = check.terminal_status_reason(self.api.status, check.status_owner(self.api.status))
                    self.assertIsNotNone(reason)
                    check.best_effort_status(OTHER, 'stale')
                    self.assertEqual(check.terminal_status_reason(self.api.status, check.status_owner(self.api.status)), reason)

    def test_collapsed_reason_remains_parseable_and_duplicate_rejected(self):
        self.start()
        check.best_effort_status(HEAD, 'success', completed_at='2026-10-07T13:09:19Z')
        self.api.pr['head']['sha'] = OTHER
        check.best_effort_status(OTHER, 'stale')
        comment = self.api.status
        owner = check.status_owner(comment)
        self.assertNotIn('**Reason:**', self.visible(comment['body']))
        self.assertEqual(check.terminal_status_reason(comment, owner), check.STATUS_REASONS['stale'])
        comment['body'] += '\n**Reason:** ' + check.STATUS_REASONS['stale']
        self.assertIsNone(check.terminal_status_reason(comment, owner))

    def test_unchanged_commit_retains_visible_integration_caveat(self):
        self.start()
        check.best_effort_status(HEAD, 'success', completed_at='2026-10-07T13:09:19Z')
        self.api.base_tip = OTHER
        check.best_effort_status(HEAD, 'stale')
        visible = self.visible(self.api.status['body'])
        self.assertIn('Claude Review passed — base advanced', visible)
        self.assertIn('Integration with the current baseline has not been reviewed.', visible)
