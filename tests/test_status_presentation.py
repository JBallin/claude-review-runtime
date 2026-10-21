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

    def test_heading_is_exact_across_states_and_history(self):
        owner = self.start()
        for state in ('in_progress', 'success', 'action_required', 'failure',
                      'publication_incomplete', 'stale'):
            for history in (None, (OTHER, 'main', 'success'), (OTHER, 'main', 'action_required')):
                for available in (True, False):
                    for running in (False, True):
                        with self.subTest(state=state, history=history, available=available, running=running):
                            body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                                owner_running=running, last_review=history, check_available=available)
                            self.assertEqual([line for line in body.splitlines() if line.startswith('#')],
                                             ['## Claude Review'])
                            self.assertEqual(check.status_state({'body': body}), state)
                            self.assertEqual(check.status_owner({'body': body}), owner)
                            self.assertIn('<summary>ℹ️ Details</summary>\n\n<br/>\n\n', body)

    def test_current_running_attempt_stays_separate_from_history(self):
        for owner in (None, self.start(PRESENTATION_START=1791316369000000000)):
            for state in ('in_progress', 'stale'):
                for result in ('success', 'action_required'):
                    with self.subTest(owner=owner, state=state, result=result):
                        body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                            owner_running=True, last_review=(OTHER, 'main', result),
                            last_review_details={'completed_at': '2026-10-06T19:00:00Z'})
                        visible, details = body.split('<details>', 1)
                        self.assertIn('| 🔄 **Running**' if owner else '| 🔄 **[Running](https://github.com/owner/repo/actions/runs/5)**', visible)
                        self.assertIn(f'| {check.commit_link(HEAD)} |', visible)
                        self.assertNotIn(check.commit_link(OTHER), visible)
                        self.assertNotIn('Completed', visible)
                        self.assertIn('**Previous completed review**', details)
                        self.assertIn(check.commit_link(OTHER), details)
                        self.assertIn(check.relative_time('2026-10-06T19:00:00Z'), details)
                        if owner:
                            self.assertIn(' started ' + check.relative_time('2026-10-06T19:52:49Z'), visible)
                        else:
                            self.assertNotIn(' started ', visible)
                        if state == 'stale':
                            self.assertIn('Review coverage of the current head and baseline is not established.', visible)

    def test_manual_scope_follows_completed_history_not_unfinished_attempt(self):
        from test_claude_review_check import presentation_owner, BASE_TIP, NEWER
        owner = presentation_owner(kind="issue_comment", target=11, head=HEAD, base=NEWER, run=6)
        history = {'base_sha': BASE_TIP, 'run_url': 'https://github.com/owner/repo/actions/runs/5'}
        for state in ('in_progress', 'failure', 'publication_incomplete', 'stale'):
            for result in ('success', 'action_required'):
                for has_history in (True, False):
                    with self.subTest(state=state, result=result, history=has_history):
                        body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                            owner_running=state == 'stale',
                            last_review=(OTHER, 'main', result) if has_history else None,
                            last_review_details=history if has_history else None)
                        visible, details = body.split('<details>', 1)
                        self.assertNotIn(check.CAPTURED_PATCH_SCOPE, visible)
                        self.assertNotIn('✅ **Completed**', visible)
                        self.assertEqual(check.status_state({'body': body}), state)
                        self.assertEqual(check.status_owner({'body': body}), owner)
                        if has_history:
                            current, previous = details.split('**Previous completed review**')
                            self.assertNotIn(check.CAPTURED_PATCH_SCOPE, current)
                            self.assertEqual(previous.count(check.CAPTURED_PATCH_SCOPE), 1)
                            self.assertIn(check.commit_link(OTHER), previous)
                            self.assertIn('[Reviewed](https://github.com/owner/repo/actions/runs/5)', previous)
                            self.assertIn('[Attempt](https://github.com/owner/repo/actions/runs/6)', current)
                            self.assertEqual(check.last_completed_review({'body': body}), (OTHER, 'main', result))
                        else:
                            self.assertNotIn(check.CAPTURED_PATCH_SCOPE, body)
                            self.assertIsNone(check.last_completed_review({'body': body}))

    def test_refresh_cannot_borrow_another_generations_trigger(self):
        owner = self.start(TRIGGER_LABEL='Draft marked ready')
        body = check.status_comment_body(HEAD, 'main', 'stale', owner=owner,
            owner_running=True, trigger_label='PR opened for review',
            last_review=(OTHER, 'main', 'success'),
            last_review_details={'generation': 'f' * 64, 'trigger': 'Draft marked ready'})
        self.assertIn('| Not recorded |', self.visible(body))
        self.assertIn('**Review trigger:** Draft marked ready', body.split('<details>')[1])

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
                self.assertIn(f'| {check.commit_link(HEAD)} |', visible)
                timestamp = check.relative_time('2026-10-07T13:09:19Z')
                self.assertIn(timestamp, visible)
                self.assertEqual(body.count(timestamp), 1)
                expected = 'Claude Review'
                heading = next(line for line in visible.splitlines() if line.startswith('## '))
                self.assertEqual(heading, '## ' + expected)
                self.assertIn(f'⚠️ This review covers commit {check.commit_link(HEAD)}. '
                              f'The current PR commit {check.commit_link(OTHER)} has not been reviewed by it.', visible)
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
                latest = ('Claude is reviewing captured commit' if state == 'in_progress'
                          else '| ❌ **Incomplete**')
                self.assertIn(latest, visible)
                self.assertNotIn('✅ **Completed**', visible)
                self.assertNotIn('Claude Review passed', visible)
                self.assertNotIn('## Last Claude review:', visible)
                if state != 'in_progress':
                    self.assertNotIn('**Reason:**', visible)
                    self.assertIn('**Reason:**', body.split('<details>', 1)[1])
                    self.assertIn('**Note:** ' + check.INCOMPLETE_NOTE, body)
                    reason = check.terminal_status_reason(self.api.status, check.status_owner(self.api.status))
                    self.assertIsNotNone(reason)
                    check.best_effort_status(OTHER, 'stale')
                    self.assertEqual(check.terminal_status_reason(self.api.status, check.status_owner(self.api.status)), reason)
                else:
                    self.assertIn('## Claude Review', visible)
                    self.assertIn(f'Claude is reviewing captured commit {check.commit_link(HEAD)}.', visible)
                    self.assertIn('Review coverage of the current head and baseline is not established.', visible)
                    self.assertIn('**Previous completed review**', body.split('<details>', 1)[1])
                    self.assertEqual(check.status_state(self.api.status), 'stale')
                    self.assertTrue(check.status_owner_running(self.api.status))
                    self.assertEqual(visible.count('Running'), 1)
                    self.assertEqual(visible.count('coverage'), 1)

    def test_running_copy_is_compact_but_unavailable_check_warning_remains(self):
        for history in (None, (OTHER, 'main', 'success')):
            for available in (True, False):
                with self.subTest(history=history, available=available):
                    body = check.status_comment_body(HEAD, 'main', 'in_progress',
                        last_review=history, check_available=available)
                    visible = self.visible(body)
                    self.assertIn('## Claude Review', visible)
                    self.assertNotIn('Claude is reviewing this commit.', visible)
                    if available:
                        self.assertNotIn('Claude is reviewing this commit', visible)
                    else:
                        self.assertIn('authoritative Check publication is unavailable.', visible)
                    if history:
                        self.assertIn('**Previous completed review**', body.split('<details>', 1)[1])
                        self.assertNotIn('✅ **Completed**', visible)
                    else:
                        self.assertIn('| 🔄 **[Running](https://github.com/owner/repo/actions/runs/5)** |', visible)

    def test_failure_reason_replaces_repetition_but_missing_reason_keeps_fallback(self):
        owner = self.start()
        for reason in (None, check.STATUS_REASONS['incomplete'], check.STATUS_REASONS['stale']):
            with self.subTest(reason=reason):
                body = check.status_comment_body(HEAD, 'main', 'failure', owner=owner,
                    reason=reason, last_review=(OTHER, 'main', 'success'))
                visible = self.visible(body)
                fallback = 'This review did not complete reliably; this commit is not approved by it.'
                self.assertIn('## Claude Review', visible)
                self.assertIn('**Previous completed review**', body.split('<details>', 1)[1])
                if reason == check.STATUS_REASONS['incomplete']:
                    self.assertIn('| ❌ **Incomplete**', visible)
                    self.assertNotIn('**Reason:**', visible)
                    details = body.split('<details>', 1)[1]
                    self.assertIn('**Reason:** ' + reason, details)
                    self.assertIn('**Note:** ' + check.INCOMPLETE_NOTE, details)
                    self.assertNotIn(fallback, visible)
                    self.assertEqual(check.terminal_status_reason({**self.api.status, 'body': body}, owner), reason)
                else:
                    self.assertIn(fallback, visible)
                    self.assertNotIn('**Reason:**', visible)

    def test_incomplete_note_uses_approved_conditional_wording(self):
        self.assertEqual(check.INCOMPLETE_NOTE,
                         "If this PR changes Claude’s review workflow, including Runtime pins, "
                         "an incomplete review is expected.")

    def test_reason_note_and_metadata_order_tracks_incomplete_presentation(self):
        owner = self.start()
        histories = (None, (OTHER, 'release/old', 'success'), (OTHER, 'release/old', 'action_required'))
        for state, reason in (('failure', check.STATUS_REASONS['incomplete']),
                              ('publication_incomplete', check.STATUS_REASONS['check_publication_failed'])):
            for history in histories:
                with self.subTest(state=state, history=history):
                    body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                                                     last_review=history, reason=reason)
                    visible, details = body.split('<details>', 1)
                    self.assertNotIn('**Reason:**', visible)
                    self.assertNotIn('**Note:**', visible)
                    if history:
                        self.assertIn('| ❌ **Incomplete**', visible)
                        self.assertIn('**Previous completed review**', details)
                    else:
                        self.assertIn('❌ **Incomplete**', visible)
                    reason_line = '**Reason:** ' + reason
                    note_line = '**Note:** ' + check.INCOMPLETE_NOTE
                    metadata = '**Current attempt**' if history else '**Started:**'
                    self.assertEqual(body.count(reason_line), 1)
                    self.assertEqual(body.count(note_line), 1)
                    self.assertLess(details.index(reason_line), details.index(note_line))
                    self.assertLess(details.index(note_line), details.index(metadata))
                    self.assertIn(reason_line + '\n' + note_line + '\n\n' + metadata, details)

    def test_success_findings_running_and_coverage_only_stale_have_no_incomplete_note(self):
        owner = self.start()
        for state, reason in (('success', None), ('action_required', None), ('in_progress', None),
                              ('stale', check.STATUS_REASONS['stale']),
                              ('stale', check.STATUS_REASONS['base_advanced'])):
            with self.subTest(state=state, reason=reason):
                body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                    owner_running=state == 'in_progress', reason=reason,
                    last_review=(OTHER, 'main', 'success') if state == 'stale' else None)
                self.assertNotIn('**Note:**', body)

    def test_stale_running_without_history_still_leads_with_active_attempt(self):
        self.start()
        self.api.pr['head']['sha'] = OTHER
        check.best_effort_status(OTHER, 'stale')
        visible = self.visible(self.api.status['body'])
        self.assertIn('## Claude Review', visible)
        self.assertIn(f'captured commit {check.commit_link(HEAD)}', visible)
        self.assertIn('current head and baseline is not established', visible)
        self.assertNotIn('**Last completed review**', visible)
        started = check.relative_time(check.owner_start_time(check.status_owner(self.api.status)))
        self.assertIn(f'| 🔄 **Running** started {started} | {check.commit_link(HEAD)} | Not recorded |', visible)
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
            self.assertIn('| Status | Reviewed commit | Review trigger |', visible)
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
        for state, label in (('in_progress', '🔄 **Running**'),
                             ('failure', '❌ **Incomplete**'),
                             ('publication_incomplete', '❌ **Incomplete**'),
                             ('stale', '**Not reviewed**')):
            with self.subTest(state=state):
                body = check.status_comment_body(HEAD, 'main', state)
                visible = self.visible(body)
                word = 'Running' if state == 'in_progress' else 'Not reviewed' if state == 'stale' else 'Incomplete'
                linked_label = label.replace(f'**{word}**', f'**[{word}](https://github.com/owner/repo/actions/runs/5)**')
                self.assertIn(f'| {linked_label} | {check.commit_link(HEAD)} | Not recorded |', visible)
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
                self.assertIn('<details>\n<summary>ℹ️ Details</summary>\n\n<br/>\n\n', body)
                self.assertEqual(body.count('<br/>'), 1)
                details = body.split('<details>')[1].split('</details>')[0]
                label = 'Reviewed' if state in ('success', 'action_required') else 'Attempt'
                self.assertIn(f'[{label}]({attempt_url})', details)
                if state == 'stale':
                    self.assertIn(f'**[Reviewed]({reviewed_url}) target:**', details)
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
        self.assertIn(check.TARGET_TIP_WARNING, visible)
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


class DetailsProvenanceTests(unittest.TestCase):
    setUp = StatusPresentationTests.setUp
    start = StatusPresentationTests.start
    visible = StatusPresentationTests.visible
    def test_linked_snapshot_and_compact_metadata(self):
        owner = self.start()
        body = check.status_comment_body(HEAD, 'main', 'success', owner=owner,
                                        trigger_label='Manual request',
                                        completed_at='2026-10-07T13:09:19Z')
        url = f"https://github.com/owner/repo/commit/{HEAD}"
        self.assertIn(f'| [`{HEAD[:7]}`]({url}) |', self.visible(body))
        details = body.split('<details>')[1].split('</details>')[0]
        self.assertNotIn('**Current commit:**', details)
        self.assertNotIn('**PR commit:**', details)
        self.assertNotIn('**Started:**', details)
        self.assertIn(f'**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`{owner["base"][:7]}`]'
                      f'(https://github.com/owner/repo/commit/{owner["base"]}) on `main`', details)
        self.assertIn('**Result:** No findings.  \n**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:**', details)
        self.assertNotIn('workflow run]', details)
        self.assertEqual(check.status_state({'body': body}), 'success')

    def test_metadata_is_one_hard_broken_group_across_states(self):
        owner = self.start(PRESENTATION_START=1791316369000000000)
        for state in ('in_progress', 'success', 'action_required', 'failure', 'publication_incomplete', 'stale'):
            for history in (None, (OTHER, 'release/old', 'success'), (OTHER, 'release/old', 'action_required')):
                body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                    owner_running=state in ('in_progress', 'stale'), last_review=history,
                    last_review_details={'base_sha': 'c' * 40, 'run_url': 'https://github.com/owner/repo/actions/runs/4'},
                    base_sha='e' * 40, reason=check.STATUS_REASONS['stale'] if state == 'stale' else None)
                details = body.split('<details>')[1].split('</details>')[0]
                self.assertTrue(details.startswith('\n<summary>ℹ️ Details</summary>\n\n<br/>\n\n'))
                self.assertEqual(body.count('<br/>'), 1)
                self.assertNotIn('\n\n[', details)
                self.assertIn('](https://github.com/owner/repo/actions/runs/', details)
                blocks = [block for block in details.split('\n\n')
                          if '**Current attempt**' in block or '**Previous completed review**' in block]
                if history and state not in ('success', 'action_required'):
                    self.assertEqual(len(blocks), 2)
                    self.assertTrue(blocks[0].startswith('**Current attempt**  \n'))
                    self.assertTrue(blocks[1].startswith('**Previous completed review**  \n'))
                    self.assertIn(blocks[0] + '\n\n' + blocks[1], details)
                for block in blocks:
                    for line in block.splitlines()[:-1]:
                        self.assertTrue(line.endswith('  '), line)

    def test_new_attempt_preserves_distinct_historical_provenance(self):
        owner = self.start(GITHUB_RUN_ID=6)
        for state in ('in_progress', 'failure', 'publication_incomplete', 'stale'):
            for result in ('success', 'action_required'):
                body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                    owner_running=state in ('in_progress', 'stale'),
                    last_review=(OTHER, 'release/old', result),
                    last_review_details={'base_sha': 'c' * 40, 'run_url': 'https://github.com/owner/repo/actions/runs/4',
                                         'completed_at': '2026-10-07T13:09:19Z', 'trigger': 'Draft marked ready'})
                visible, details = body.split('<details>', 1)
                self.assertIn(f'| {check.commit_link(HEAD)} |', visible)
                self.assertNotIn(check.commit_link(OTHER), visible)
                current, previous = details.split('**Previous completed review**')
                self.assertIn('**Current attempt**', current)
                self.assertIn('**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:**', current)
                self.assertIn('**Reviewed commit:** ' + check.commit_link(OTHER), previous)
                self.assertIn('**Review trigger:** Draft marked ready', previous)
                self.assertIn('**[Reviewed](https://github.com/owner/repo/actions/runs/4) against:** ' + check.commit_link('c' * 40) + ' on `release/old`', previous)
                self.assertNotIn('https://github.com/owner/repo/actions/runs/6', previous)
                self.assertEqual('**Attempt started:**' in current, state not in ('in_progress', 'stale'))
                self.assertEqual(check.status_state({'body': body}), state)

    def test_shared_reviewed_run_is_not_duplicated(self):
        owner = self.start()
        url = 'https://github.com/owner/repo/actions/runs/5'
        body = check.status_comment_body(HEAD, 'main', 'stale', owner=owner,
            last_review=(HEAD, 'main', 'success'),
            last_review_details={'base_sha': owner['base'], 'run_url': url})
        self.assertEqual(body.count(url), 1)
        self.assertIn(f'**[Reviewed]({url}) against:**', body)
        self.assertNotIn('[Attempt workflow run]', body)
        self.assertNotIn('**Attempt started:**', body)

    def test_legacy_history_does_not_borrow_attempt_provenance(self):
        owner = self.start()
        body = check.status_comment_body(HEAD, 'main', 'failure', owner=owner,
                                       last_review=(OTHER, 'release/old', 'success'))
        self.assertIn('**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:**', body)
        self.assertNotIn('[Reviewed workflow run]', body)
        self.assertNotIn('**Reviewed against:**', body)
        self.assertNotIn('**Completed:**', body)
        self.assertNotIn('**Review trigger:**', body)

    def test_rerun_generations_share_run_identity_but_keep_contexts_distinct(self):
        owner = self.start()
        url = 'https://github.com/owner/repo/actions/runs/5'
        for state in ('in_progress', 'failure', 'publication_incomplete', 'stale'):
            with self.subTest(state=state):
                body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                    last_review=(HEAD, 'main', 'success'),
                    last_review_details={'base_sha': owner['base'], 'run_url': url,
                                         'generation': 'f' * 64})
                self.assertNotIn('**Attempt commit:**', body)
                self.assertNotIn('**Attempt baseline:**', body)
                self.assertIn(f'**[Reviewed]({url}) against:**', body)
                if state != 'stale':
                    current, previous = body.split('**Previous completed review**')
                    self.assertEqual('**Current attempt**' in current, state != 'in_progress')
                    self.assertIn(f'**[Running]({url})**' if state == 'in_progress' else f'**[Attempt]({url}) started:**', current)
                    self.assertNotIn(f'[Reviewed]({url})', current)
                    self.assertIn(f'**[Reviewed]({url}) against:**', previous)
                else:
                    self.assertIn(f'**[Attempt]({url}) started:**', body)
                # Two contexts can share a workflow run (different generations).
                self.assertEqual(body.count(url), 2)

    def test_legacy_shared_run_keeps_current_and_previous_contexts_linked(self):
        owner = self.start()
        url = 'https://github.com/owner/repo/actions/runs/5'
        for state in ('in_progress', 'failure', 'publication_incomplete'):
            body = check.status_comment_body(HEAD, 'main', state, owner=owner,
                last_review=(OTHER, 'release/old', 'success'),
                last_review_details={'run_url': url})
            current, previous = body.split('**Previous completed review**')
            self.assertIn('**Current attempt**', current)
            self.assertIn(f'**[Attempt]({url}) baseline:**', current)
            self.assertIn(f'**[Reviewed]({url}) target:**', previous)
            self.assertNotIn('[Reviewed]', current)

    def test_legacy_attempt_without_metadata_keeps_plain_heading_and_honest_status(self):
        body = check.status_comment_body(HEAD, 'main', 'stale',
            last_review=(HEAD, 'main', 'success'),
            last_review_details={'run_url': 'https://github.com/owner/repo/actions/runs/4'})
        self.assertIn('**[Attempt](https://github.com/owner/repo/actions/runs/5) status:** Not recorded', body)
        self.assertIn('**[Reviewed](https://github.com/owner/repo/actions/runs/4) target:**', body)
        self.assertNotIn('[Current attempt]', body)
        self.assertNotIn('workflow run]', body)

    def test_unavailable_run_links_stay_plain_without_borrowing_history(self):
        os.environ.pop('DETAILS_URL', None)
        for state in ('success', 'action_required', 'in_progress', 'failure', 'publication_incomplete', 'stale'):
            with self.subTest(state=state):
                body = check.status_comment_body(HEAD, 'main', state,
                    last_review=(OTHER, 'release/old', 'success') if state == 'stale' else None,
                    last_review_details={'base_sha': 'c' * 40})
                self.assertNotIn('/actions/runs/', body)
                self.assertNotIn('(None)', body)
                self.assertNotIn('[](', body)
                if state in ('success', 'action_required', 'stale'):
                    self.assertIn('**Reviewed against:**', body)
        marker = check.completion_marker('owner/repo', 7, HEAD, 'main', 'd' * 40, 'c' * 40)
        for url in (None, ''):
            body = check.completion_comment_body('owner/repo', HEAD, 'main', 'd' * 40, marker, url)
            self.assertIn('**Reviewed against:**', body)
            self.assertNotIn('/actions/runs/', body)
            self.assertNotIn('(None)', body)
