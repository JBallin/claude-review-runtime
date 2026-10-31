"""Manual selection and informational execution evidence; no provider calls."""
import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_claude_review_check import check, HEAD, presentation_owner
import test_claude_review_workflows as w


class SelectionTests(unittest.TestCase):
    def test_short_forms_have_literal_selections(self):
        for body, (model, effort) in check.MANUAL_COMMANDS.items():
            for text in (body, body.upper(), " \t" + body + "\r\n"):
                self.assertEqual(check.manual_selection(text), dict(requested_model=model, requested_effort=effort))

    def test_invalid_manual_arguments_reject_the_whole_request(self):
        for body in ('/claude-review invalid high', '/claude-review opus invalid',
                     '/claude-review high opus', '/claude-review effort high',
                     '/claude-review opus --effort', '/claude-review opus high extra'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                check.resolve_configuration(body, 'haiku', 'low')

    def test_literal_resolved_args_and_ambient_model_guard(self):
        for body in check.MANUAL_COMMANDS:
            config = check.resolve_configuration(body)
            model, effort = config['requested_model'], config['requested_effort']
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'request.json'
                path.write_text(json.dumps(dict(trigger_comment=body, selection_config=config)))
                with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': body, 'ANTHROPIC_MODEL': 'sonnet'}, clear=True), mock.patch.object(check, 'write_output') as out:
                    self.assertEqual(check.cmd_selection(str(path)), 1 if model != 'sonnet' else 0)
                with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': body}, clear=True), mock.patch.object(check, 'write_output') as out:
                    self.assertEqual(check.cmd_selection(str(path)), 0)
                    self.assertIn(mock.call('model_args', '--model ' + model), out.call_args_list)
                    self.assertIn(mock.call('effort_args', '--effort ' + effort), out.call_args_list)
                    path.write_text(json.dumps(dict(trigger_comment=body, selection_config={**config, 'requested_effort': 'bad'})))
                    self.assertEqual(check.cmd_selection(str(path)), 1)

    def test_effort_display_capitalization_preserves_lowercase_selection_and_args(self):
        for effort, display in (('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
                                ('xhigh', 'Xhigh'), ('max', 'Max')):
            with self.subTest(effort=effort):
                command = '/claude-review opus ' + effort
                selected = check.manual_selection(command.upper())
                self.assertEqual(selected, {'requested_model': 'opus', 'requested_effort': effort})
                with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': command}, clear=True), mock.patch.object(check, 'write_output') as out:
                    self.assertEqual(check.cmd_selection(), 0)
                    self.assertIn(mock.call('requested_effort', effort), out.call_args_list)
                    self.assertIn(mock.call('effort_args', '--effort ' + effort), out.call_args_list)
                owner, record = self.record(requested_effort=effort)
                original = copy.deepcopy(record)
                for state in ('in_progress', 'success'):
                    body = check.status_comment_body(HEAD, 'main', state, owner=owner, selection=record)
                    self.assertIn('**Model:** Opus · **Effort:** ' + display, body.split('<details>')[1])
                    self.assertNotIn('**Effort:**', body.split('<details>')[0])
                    self.assertEqual(check.current_selection(owner, {'body': body}), original)
                self.assertEqual(record, original)
                self.assertIsNone(record['observed_effort'])

    def record(self, models=('claude-opus-4-7',), usage=None, transform=None, **changes):
        owner = presentation_owner(kind='issue_comment', target=12)
        command = '/claude-review'
        if changes.get('requested_model', 'opus'):
            command += ' ' + changes.get('requested_model', 'opus')
        if changes.get('requested_effort', 'high'):
            command += ' ' + changes.get('requested_effort', 'high')
        stream = [{'type': 'system', 'subtype': 'init', 'session_id': 'session', 'model': models[0] if models else None},
                  *[{'type':'assistant','session_id':'session','message':{'model':m}} for m in models],
                  {'type':'result','session_id':'session','modelUsage':usage if usage is not None else {m:{'inputTokens':1} for m in models}}]
        if transform:
            transform(stream)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'execution.json'
            path.write_text(json.dumps(stream))
            with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': command, 'GITHUB_RUN_ATTEMPT':'2'}, clear=True):
                record = check.selection_record(owner, str(path))
        record.update(changes)
        return owner, record

    def test_observation_unknown_and_multiple_never_change_verdict(self):
        for models, usage, evidence in [(('claude-opus-4-7',), None, 'observed'),
                (('claude-sonnet-4-6',), None, 'observed'),
                (('claude-opus-4-7',), {'claude-haiku-4-5':{'inputTokens':1}}, 'mixed'),
                (('claude-opus-4-7',), {}, 'unknown'),
                (('<synthetic>',), None, 'unknown'),
                (('claude-opus-4-7',), {'claude-opus-4-7':{'inputTokens':0}}, 'unknown')]:
            owner, record = self.record(models, usage)
            self.assertEqual(record['model_evidence'], evidence)
            self.assertEqual(check.valid_selection(record, owner), record)
            body = check.status_comment_body(HEAD, 'main', 'success', owner=owner, selection=record)
            self.assertIn('**Completed**', body)
            self.assertIn('**Effort:** High', body)
            self.assertNotIn('**Observed effort:**', body)
            self.assertIn('**Model:** Opus', body)
            if models == ('claude-opus-4-7',):
                self.assertIn('**Model:** Opus · **Effort:** High', body.split('<details>')[1])
            else:
                self.assertIn('⚠️ **Model', body.split('<details>')[0])
            if models == ('claude-sonnet-4-6',):
                self.assertIn('⚠️ **Model', body)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(check.current_selection(owner, {'body':body}), record)
                other = copy.deepcopy(owner); other['generation'] = 'f' * 64
                self.assertIsNone(check.current_selection(other, {'body':body}))
                marker = next(line for line in body.splitlines() if line.startswith(check.SELECTION_PREFIX))
                self.assertIsNone(check.current_selection(owner, {'body':body+'\n'+marker}))

    def test_new_request_and_failed_rerun_do_not_borrow_previous_observations(self):
        owner, record = self.record()
        body = check.status_comment_body(HEAD, 'main', 'success', owner=owner, selection=record)
        for generation in (owner['generation'], 'f' * 64):
            current = {**owner, 'generation': generation}
            with mock.patch.dict(os.environ, {'TRIGGER_COMMENT':'/claude-review opus',
                    'GITHUB_RUN_ATTEMPT':'3', 'REVIEW_RESULT':'failure'}, clear=True):
                fresh = check.current_selection(current, {'body':body})
                self.assertEqual(fresh['generation'], generation)
                self.assertEqual(fresh['requested_model'], 'opus')
                self.assertEqual(fresh['models'], [])
                self.assertIsNone(fresh['execution_attempt'])
                self.assertIsNone(fresh['primary_model_evidence'])
        # A publisher-only rerun can retain an earlier execution only through
        # the explicit trusted review-job output, never ambient comment state.
        with mock.patch.dict(os.environ, {'SELECTION_EVIDENCE':json.dumps(record),
                'TRIGGER_COMMENT':'/claude-review opus', 'GITHUB_RUN_ATTEMPT':'3'}, clear=True):
            self.assertEqual(check.current_selection(owner), record)

    def test_malformed_receipts_are_not_rendered(self):
        owner, record = self.record()
        for changes in ({'execution_attempt':True}, {'models':['<script>']}, {'requested_effort':'ultra'},
                        {'generation':'other'}, {'model_evidence':'unknown'}, {'observed_effort':'high'}, {'extra':'value'}):
            candidate = {**record, **changes}
            self.assertIsNone(check.valid_selection(candidate, owner))

    def test_primary_evidence_ignores_auxiliary_usage_but_retains_provenance(self):
        usage = {'claude-opus-5-5': {'inputTokens': 1}, 'claude-haiku-4-5': {'outputTokens': 1}}
        owner, record = self.record(models=('claude-opus-5-5',), usage=usage)
        self.assertEqual(record['models'], sorted(usage))
        self.assertEqual(record['model_evidence'], 'mixed')
        self.assertEqual(record['primary_model_evidence'], {
            'initialization': 'claude-opus-5-5', 'assistant': ['claude-opus-5-5'], 'attempt': 2})
        self.assertIsNone(record['observed_effort'])
        body = check.status_comment_body(HEAD, 'main', 'success', owner=owner, selection=record)
        for hidden in ('', '<!--\nHaiku requested by an auxiliary call\n-->\n'):
            with self.subTest(multiline_comment=bool(hidden)):
                visible = re.sub(r'<!--.*?-->', '', (hidden + body).split('<details>')[0], flags=re.DOTALL)
                self.assertNotIn('**Model:**', visible)
                self.assertNotIn('**Effort:**', visible)
                self.assertNotIn('Haiku', visible)
                self.assertNotIn('requested', visible)
                details = re.sub(r'<!--.*?-->', '', body.split('<details>')[1], flags=re.DOTALL)
                self.assertIn('**Model:** Opus · **Effort:** High', details)
                self.assertNotIn('Haiku', details)
        self.assertEqual(check.current_selection(owner, {'body': body}), record)

    def test_primary_evidence_survives_unavailable_aggregate_usage(self):
        for usage in ({}, [], {'claude-opus-5-5': None},
                      {'<invalid>': {'inputTokens': 1}},
                      {'claude-opus-5-5': {'inputTokens': 0}},
                      {f'claude-auxiliary-{i}': {'inputTokens': 1} for i in range(9)}):
            with self.subTest(usage=usage):
                owner, record = self.record(models=('claude-opus-5-5',), usage=usage)
                self.assertEqual(record['primary_model_evidence'], {
                    'initialization': 'claude-opus-5-5',
                    'assistant': ['claude-opus-5-5'], 'attempt': 2})
                self.assertEqual(record['models'], [])
                self.assertIsNone(record['execution_attempt'])
                self.assertEqual(record['model_evidence'], 'unknown')
                self.assertIsNone(record['observed_effort'])
                self.assertEqual(check.valid_selection(record, owner), record)
                self.assertEqual(check.selection_notice(record), [])
                self.assertEqual(check.selection_details(record),
                                 ['**Model:** Opus · **Effort:** High'])

    def test_primary_fallback_and_conflicting_sources_are_visible(self):
        for models, transform, expected in (
                (('claude-sonnet-4-6',), None, 'Model mismatch:** Sonnet observed'),
                (('claude-opus-5-5',), lambda s: s[0].update(model='claude-sonnet-4-6'),
                 'Model ambiguous:** Opus / Sonnet observed'),
                (('claude-opus-5-5', 'claude-sonnet-4-6'), None,
                 'Model ambiguous:** Opus / Sonnet observed')):
            with self.subTest(expected=expected):
                owner, record = self.record(models=models, transform=transform)
                for running in (False, True):
                    notice = check.selection_notice(record, running=running)[0]
                    self.assertEqual('⚠️ **' + expected, notice)

    def test_model_warning_versions_are_kept_only_to_distinguish_same_family_evidence(self):
        for models, expected in (
                (('claude-opus-4-7', 'claude-sonnet-5-5'), 'Model ambiguous:** Opus / Sonnet observed'),
                (('claude-opus-4-7', 'claude-opus-5-5'), 'Model ambiguous:** Opus 4.7 / Opus 5.5 observed'),
                (('claude-sonnet-5-5-20261001',), 'Model mismatch:** Sonnet observed')):
            with self.subTest(models=models):
                owner, record = self.record(models=models)
                original = copy.deepcopy(record)
                self.assertIn(expected, ' '.join(check.selection_notice(record)))
                self.assertEqual(record, original)
                self.assertEqual(check.valid_selection(record, owner), original)
        self.assertEqual(check.model_label('claude-unrecognized-1'), '`claude-unrecognized-1`')

    def test_missing_or_invalid_primary_sources_never_confirm_usage_bag(self):
        for transform in (
                lambda s: s[0].update(model='opus'),
                lambda s: s[1].update(session_id='other'),
                lambda s: s[1].update(error='failure'),
                lambda s: s.append(dict(s[0])),
                lambda s: s.pop(1),
                lambda s: s.pop(),
                lambda s: s[1]['message'].update(model=None)):
            owner, record = self.record(transform=transform)
            self.assertIsNone(record['primary_model_evidence'])
            self.assertEqual(check.valid_selection(record, owner), record)
            self.assertIn('⚠️ **Model unknown:** primary evidence missing',
                          check.selection_notice(record)[0])
        # Historical global usage records cannot establish a primary model.
        owner, record = self.record()
        del record['primary_model_evidence']
        self.assertEqual(check.valid_selection(record, owner), record)
        self.assertIn('primary evidence missing', check.selection_notice(record)[0])

    def test_assistant_source_without_initialization_model_and_nested_auxiliary_turn(self):
        def transform(stream):
            stream[0].pop('model')
            stream.insert(2, {'type': 'assistant', 'session_id': 'subagent',
                              'parent_tool_use_id': 'tool', 'message': {'model': 'claude-haiku-4-5'}})
        owner, record = self.record(transform=transform)
        self.assertEqual(record['primary_model_evidence']['assistant'], ['claude-opus-4-7'])
        self.assertIsNone(record['primary_model_evidence']['initialization'])
        self.assertEqual(check.selection_notice(record), [])
        self.assertEqual(check.selection_details(record), ['**Model:** Opus · **Effort:** High'])
        self.assertEqual(check.valid_selection(record, owner), record)

    def test_primary_receipt_validation_and_running_selection(self):
        owner, record = self.record()
        for primary in ({}, {'initialization': None, 'assistant': [], 'attempt': 2},
                        {**record['primary_model_evidence'], 'attempt': True},
                        {**record['primary_model_evidence'], 'attempt': 3},
                        {**record['primary_model_evidence'], 'initialization': '<bad>'}):
            self.assertIsNone(check.valid_selection({**record, 'primary_model_evidence': primary}, owner))
        with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': '/claude-review opus high'}, clear=True):
            running = check.selection_record(owner)
        for state, extra in (('in_progress', {}), ('stale', {'owner_running': True})):
            body = check.status_comment_body(HEAD, 'main', state, owner=owner, selection=running, **extra)
            self.assertIn('**Model:** Opus · **Effort:** High', body)
            self.assertNotIn('primary evidence missing', body)
        self.assertIn('primary evidence missing', check.selection_notice(running)[0])

    def test_plain_comment_displays_explicit_runtime_settings(self):
        owner, record = self.record(models=('claude-sonnet-5-5',), requested_model='sonnet', requested_effort='medium')
        for state in ('success', 'in_progress', 'failure', 'stale'):
            body = check.status_comment_body(HEAD, 'main', state, owner=owner, selection=record)
            main, details = body.split('<details>')
            self.assertNotIn('**Model:**', main)
            self.assertIn('**Model:** Sonnet · **Effort:** Medium', details)
            self.assertNotIn('Sonnet 5.5', details)
            self.assertIn(check.SELECTION_PREFIX, body)
            self.assertEqual(check.current_selection(owner, {'body': body}), record)
        with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': '/claude-review'}, clear=True):
            unknown = check.selection_record(owner)
        self.assertEqual(check.selection_details(unknown), ['**Model:** Sonnet · **Effort:** Medium'])
        self.assertEqual(check.selection_notice(unknown, running=True), [])

    def test_effort_only_selection_resolves_model_and_keeps_both_fields_visible(self):
        owner, record = self.record(models=('claude-sonnet-5-5',), requested_model='sonnet')
        body = check.status_comment_body(HEAD, 'main', 'success', owner=owner, selection=record)
        self.assertEqual(check.selection_notice(record), [])
        self.assertEqual(check.selection_details(record), ['**Model:** Sonnet · **Effort:** High'])
        self.assertNotIn('No override requested', body)
        self.assertNotIn('**Model:**', body.split('<details>')[0])
        self.assertEqual(check.current_selection(owner, {'body': body}), record)
        self.assertNotIn('**Observed effort:**', body)
        with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': '/claude-review high'}, clear=True):
            running = check.selection_record(owner)
        self.assertEqual(check.selection_details(running), ['**Model:** Sonnet · **Effort:** High'])
        self.assertEqual(check.selection_notice(running, running=True), [])

    def test_lifecycle_selection_once_and_structured_attribution(self):
        owner, record = self.record()
        for state in ('in_progress', 'success', 'action_required', 'failure'):
            body = check.status_comment_body(HEAD, 'main', state, owner=owner, selection=record)
            main, details = body.split('<details>')
            self.assertNotIn('**Model:**', main)
            self.assertNotIn('**Effort:**', main)
            self.assertEqual(details.count('**Model:**'), 1)
            self.assertEqual(details.count('**Effort:**'), 1)
            for label in ('**Requested', '**Observed', '**Model evidence attempt:', '**Result:** No findings.'):
                self.assertNotIn(label, details)
            self.assertEqual(check.current_selection(owner, {'body': body}), record)
        mixed = {**record, 'models': ['claude-haiku-5-5', 'claude-opus-5-5'], 'model_evidence': 'mixed'}
        self.assertEqual(check.selection_notice(mixed), [])
        self.assertEqual(check.selection_details(mixed), ['**Model:** Opus · **Effort:** High'])
        _, unusual = self.record(models=('claude-custom',))
        self.assertIn('`claude-custom`', check.selection_notice(unusual)[0])

    def test_legacy_receipt_remains_readable_without_configuration_or_primary_evidence(self):
        owner, record = self.record()
        record.pop('configuration')
        record.pop('primary_model_evidence')
        self.assertEqual(check.valid_selection(record, owner), record)
        body = check.status_comment_body(HEAD, 'main', 'success', owner=owner, selection=record)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(check.current_selection(owner, {'body': body}), record)
        main, details = body.split('<details>')
        self.assertIn('primary evidence missing', main)
        self.assertIn('**Model:** Opus · **Effort:** High', details)
        self.assertNotIn('Opus 4.7', details)

    def test_historical_notice_filter_emits_only_actual_single_primary_model_mismatch(self):
        _, mismatch = self.record(models=('claude-sonnet-5-5',))
        self.assertEqual(check.selection_notice(mismatch, mismatch_only=True),
            ['⚠️ **Model mismatch:** Sonnet observed'])
        _, matching = self.record()
        _, conflicting = self.record(models=('claude-opus-5-5', 'claude-sonnet-5-5'))
        _, missing = self.record()
        missing.pop('primary_model_evidence')
        _, configured = self.record()
        configured['configuration'] = {"version": 2, "requested_model": "opus", "requested_effort": "high",
            "model_source": "manual", "effort_source": "workflow", "errors": ["effort"],
            "diagnostics": [{"field": "effort", "source": "manual", "reason": "invalid"}]}
        for record in (matching, conflicting, missing, configured):
            with self.subTest(record=record):
                original = copy.deepcopy(record)
                self.assertEqual(check.selection_notice(record, mismatch_only=True), [])
                self.assertEqual(record, original)
        self.assertIn('Model ambiguous', ' '.join(check.selection_notice(conflicting)))
        self.assertIn('primary evidence missing', ' '.join(check.selection_notice(missing)))
        self.assertIn('**Invalid effort requested**', ' '.join(check.selection_notice(configured)))

    def test_manual_completion_settings_lead_details_before_baseline_and_runtime(self):
        _, record = self.record()
        original = copy.deepcopy(record)
        environment = {'RUNTIME_REPOSITORY': 'JBallin/claude-review-runtime', 'RUNTIME_SHA': 'f' * 40,
                       'RUNTIME_WORKFLOW_PATH': '.github/workflows/claude.yml'}
        with mock.patch.dict(os.environ, environment, clear=True):
            body = check.completion_comment_body('owner/repo', HEAD, 'main', 'b' * 40,
                '<!-- completion -->', 'https://github.com/owner/repo/actions/runs/5', selection=record)
        visible, details = body.split('</summary>', 1)
        settings = '**Model:** Opus · **Effort:** High'
        self.assertNotIn('**Model:**', visible)
        self.assertEqual(details.strip().splitlines()[0].rstrip(), settings)
        self.assertNotIn('<br', details)
        self.assertLess(details.index(settings), details.index('against:**'))
        self.assertLess(details.index(settings), details.index('**Runtime used:**'))
        self.assertEqual(record, original)

    def test_immutable_completion_receipt_keeps_settings_in_details_and_warning_visible(self):
        for model, expected, warning in (('claude-opus-5-5', 'Opus', False),
                                         ('claude-sonnet-5-5', 'Opus', True)):
            with self.subTest(model=model):
                _, record = self.record(models=(model,))
                original = copy.deepcopy(record)
                with mock.patch.dict(os.environ, {}, clear=True):
                    body = check.completion_comment_body(
                        'owner/repo', HEAD, 'main', 'b' * 40, '<!-- completion -->',
                        'https://github.com/owner/repo/actions/runs/5', selection=record)
                main, details = body.split('<details>')
                self.assertEqual('Model mismatch' in main, warning)
                self.assertNotIn('**Effort:**', main)
                if not warning:
                    self.assertNotIn('**Model:**', main)
                self.assertIn('**Model:** ' + expected + ' · **Effort:** High', details)
                self.assertNotIn('Observed effort', body)
                self.assertEqual(record, original)
