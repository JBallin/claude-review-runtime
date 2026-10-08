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
                self.assertIn(f'| `{HEAD[:7]}` |', visible)
                timestamp = check.relative_time('2026-10-07T13:09:19Z')
                self.assertIn(timestamp, visible)
                self.assertEqual(body.count(timestamp), 1)
                expected = 'Claude Review'
                heading = next(line for line in visible.splitlines() if line.startswith('## '))
                self.assertEqual(heading, '## ' + expected)
                self.assertIn('⚠️ This completed review doesn’t cover the current version.' if result == 'success'
                              else '⚠️ These recorded findings are from a review of a previous version.', visible)
                self.assertNotIn('**Last completed review**', visible)
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
                latest = 'Claude is reviewing captured commit' if state == 'in_progress' else '**Reason:**'
                self.assertLess(visible.index(latest), visible.index('| Status |'))
                self.assertNotIn('Claude Review passed', visible)
                self.assertNotIn('## Last Claude review:', visible)
                if state != 'in_progress':
                    reason = check.terminal_status_reason(self.api.status, check.status_owner(self.api.status))
                    self.assertIsNotNone(reason)
                    check.best_effort_status(OTHER, 'stale')
                    self.assertEqual(check.terminal_status_reason(self.api.status, check.status_owner(self.api.status)), reason)
                else:
                    self.assertIn('## 🔄 Claude Review in progress', visible)
                    self.assertIn(f'Claude is reviewing captured commit `{HEAD[:7]}`.', visible)
                    self.assertIn('Review coverage of the current head and baseline is not established.', visible)
                    self.assertLess(visible.index('in progress'), visible.index('**Last completed review**'))
                    self.assertEqual(check.status_state(self.api.status), 'stale')
                    self.assertTrue(check.status_owner_running(self.api.status))
                    self.assertEqual(visible.count('in progress'), 1)
                    self.assertEqual(visible.count('coverage'), 1)

    def test_stale_running_without_history_still_leads_with_active_attempt(self):
        self.start()
        self.api.pr['head']['sha'] = OTHER
        check.best_effort_status(OTHER, 'stale')
        visible = self.visible(self.api.status['body'])
        self.assertIn('## 🔄 Claude Review in progress', visible)
        self.assertIn(f'captured commit `{HEAD[:7]}`', visible)
        self.assertIn('current head and baseline is not established', visible)
        self.assertNotIn('**Last completed review**', visible)
        self.assertIn(f'| 🔄 **In progress** | `{HEAD[:7]}` | Not recorded |', visible)
        self.assertNotIn('Not reviewed', visible)
        self.assertEqual(visible.count('coverage'), 1)
        self.assertEqual(check.status_state(self.api.status), 'stale')
        self.assertTrue(check.status_owner_running(self.api.status))

    def test_compact_completion_and_findings_remain_distinct(self):
        for state, label in (('success', '✅ **Completed**'), ('action_required', '⚠️ **Findings**')):
            body = check.status_comment_body(HEAD, 'main', state,
                                            completed_at='2026-10-07T13:09:19Z')
            visible = self.visible(body)
            self.assertIn('## Claude Review', visible)
            self.assertIn('| Status | Commit | Review trigger |', visible)
            self.assertIn(f'| {label} ' + check.relative_time('2026-10-07T13:09:19Z'), visible)
            self.assertNotIn('| Review |', visible)
            self.assertNotIn('<br>', visible)
            self.assertNotIn('Last completed review', visible)
            if state == 'success':
                self.assertNotIn('no findings', visible.lower())
                self.assertIn('**Result:** No findings.', body.split('<details>')[1])
            else:
                self.assertIn('inline review threads', visible)

    def test_uncompleted_status_labels_are_bold(self):
        for state, label in (('in_progress', '🔄 **In progress**'),
                             ('failure', '❌ **Incomplete**'),
                             ('publication_incomplete', '❌ **Incomplete**'),
                             ('stale', '**Not reviewed**')):
            with self.subTest(state=state):
                body = check.status_comment_body(HEAD, 'main', state)
                visible = self.visible(body)
                self.assertIn(f'| {label} | `{HEAD[:7]}` | Not recorded |', visible)
                self.assertNotIn('<relative-time', visible)

    def test_workflow_links_are_collapsed_and_keep_attempt_association(self):
        owner = self.start()
        attempt_url = f"https://github.com/{owner['repo']}/actions/runs/{owner['run']}"
        reviewed_url = f"https://github.com/{owner['repo']}/actions/runs/4"
        for state in ('success', 'action_required', 'failure', 'publication_incomplete', 'in_progress', 'stale'):
            with self.subTest(state=state):
                body = check.status_comment_body(OTHER, 'main', state, owner=owner,
                    owner_running=state in ('in_progress', 'stale'),
                    last_review=(HEAD, 'main', 'success') if state == 'stale' else None,
                    last_review_details={'run_url': reviewed_url})
                self.assertNotIn('workflow run', self.visible(body).lower())
                self.assertIn('<summary>ℹ️ Details</summary>', body)
                details = body.split('<details>')[1].split('</details>')[0]
                label = 'Attempt workflow run' if state == 'stale' else 'Workflow run'
                self.assertIn(f'[{label}]({attempt_url})', details)
                if state == 'stale':
                    self.assertIn(f'[Reviewed workflow run]({reviewed_url})', details)
                self.assertEqual(body.count(attempt_url), 1)

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
        self.assertIn('## Claude Review', visible)
        self.assertIn('Integration with the current baseline has not been reviewed.', visible)
        self.assertIn(check.relative_time('2026-10-07T13:09:19Z'), visible)

    def test_current_completion_visible_and_legacy_time_absent(self):
        for state in ('success', 'action_required'):
            for when in (None, 'invalid', '2026-10-07T13:09:19Z'):
                with self.subTest(state=state, when=when):
                    body = check.status_comment_body(HEAD, 'main', state, completed_at=when)
                    visible = self.visible(body)
                    if when == '2026-10-07T13:09:19Z':
                        timestamp = check.relative_time(when)
                        self.assertIn(timestamp, visible)
                        self.assertEqual(body.count(timestamp), 1)
                    else:
                        self.assertNotIn('<relative-time', visible)

    def test_check_summary_validates_optional_completion_time(self):
        for when in (None, 'invalid', '2026-10-07T13:09:19Z'):
            summary = check.summary_lines(HEAD, 'Review result.', completed_at=when)
            if when == '2026-10-07T13:09:19Z':
                self.assertIn('**Completed:** ' + check.relative_time(when), summary)
            else:
                self.assertNotIn('**Completed:**', summary)
