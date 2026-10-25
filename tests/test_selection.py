"""Manual selection and informational execution evidence; no provider calls."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_claude_review_check import check, HEAD, presentation_owner
import test_claude_review_workflows as w


class SelectionTests(unittest.TestCase):
    def test_command_grammar_matches_queue_and_admission(self):
        for body, (model, effort) in check.MANUAL_COMMANDS.items():
            for text in (body, body.upper()):
                self.assertEqual(check.manual_selection(text), dict(requested_model=model, requested_effort=effort))
                for factory in (w.issue_comment_event, w.review_comment_event):
                    event = factory(body=text)
                    self.assertTrue(w.evaluate(w.start_check_predicate(w.MANUAL), event))
                    self.assertNotIn('ineligible', w.evaluate(w.concurrency_group(w.MANUAL), event))
                    for association in ('NONE', 'CONTRIBUTOR'):
                        self.assertFalse(w.evaluate(w.start_check_predicate(w.MANUAL), factory(body=text, association=association)))
        for body in ('/claude-review opus ', '/claude-review\topus', '/claude-review opus\n',
                     '/claude-review sonnet', '/claude-review opus --effort high',
                     '/claude-review effort high opus', '/claude-review opus; echo bad',
                     '/claude-review opus effort $(id)', '/claude-review opus effort ultra', None):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    check.manual_selection(body)
                self.assertFalse(w.evaluate(w.start_check_predicate(w.MANUAL), w.issue_comment_event(body=body)))

    def test_literal_args_and_ambient_model_guard(self):
        for body, (model, effort) in check.MANUAL_COMMANDS.items():
            selected = check.manual_selection(body)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'request.json'
                path.write_text(json.dumps(dict(trigger_comment=body, **selected)))
                with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': body, 'ANTHROPIC_MODEL': 'sonnet'}, clear=True), mock.patch.object(check, 'write_output') as out:
                    self.assertEqual(check.cmd_selection(str(path)), 1 if model else 0)
                    if not model:
                        self.assertIn(mock.call('effort_args', '--effort ' + effort if effort else ''), out.call_args_list)
                with mock.patch.dict(os.environ, {'TRIGGER_COMMENT': body}, clear=True), mock.patch.object(check, 'write_output') as out:
                    self.assertEqual(check.cmd_selection(str(path)), 0)
                    self.assertIn(mock.call('model_args', '--model opus' if model else ''), out.call_args_list)
                    path.write_text(json.dumps(dict(trigger_comment=body, **{**selected, 'requested_effort': 'bad'})))
                    self.assertEqual(check.cmd_selection(str(path)), 1)

    def record(self, models=('claude-opus-4-7',), usage=None, **changes):
        owner = presentation_owner(kind='issue_comment', target=12)
        stream = [{'type': 'system', 'subtype': 'init', 'session_id': 'session'},
                  *[{'type':'assistant','session_id':'session','message':{'model':m}} for m in models],
                  {'type':'result','session_id':'session','modelUsage':usage if usage is not None else {m:{'inputTokens':1} for m in models}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'execution.json'
            path.write_text(json.dumps(stream))
            with mock.patch.dict(os.environ, {'TRIGGER_COMMENT':'/claude-review opus effort high', 'GITHUB_RUN_ATTEMPT':'2'}, clear=True):
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
            self.assertIn('**Requested effort:** high', body)
            self.assertIn('**Observed effort:** unknown', body)
            self.assertIn('Opus requested', body)
            if models == ('claude-sonnet-4-6',):
                self.assertIn('⚠️ **Model:**', body)
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
        # A publisher-only rerun can retain an earlier execution only through
        # the explicit trusted review-job output, never ambient comment state.
        with mock.patch.dict(os.environ, {'SELECTION_EVIDENCE':json.dumps(record),
                'TRIGGER_COMMENT':'/claude-review opus', 'GITHUB_RUN_ATTEMPT':'3'}, clear=True):
            self.assertEqual(check.current_selection(owner), record)

    def test_malformed_receipts_are_not_rendered(self):
        owner, record = self.record()
        for changes in ({'execution_attempt':True}, {'models':['<script>']}, {'requested_effort':'ultra'},
                        {'generation':'other'}, {'model_evidence':'unknown'}, {'extra':'value'}):
            candidate = {**record, **changes}
            self.assertIsNone(check.valid_selection(candidate, owner))
