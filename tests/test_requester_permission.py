"""Execute the trusted workflow's permission boundary with a synthetic API."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_claude_review_workflows as w


NAME = 'Verify original requester permission'
JOBS = ('request', 'start-check', 'review', 'publish-status')


def probe(permission, job='request', login='comment-author', user_id='17', fail=False):
    script = w.run_block(w.steps(w.job(w.MANUAL, job))[NAME])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        # Seed a previously successful review. Any attempted mutation changes it
        # and is logged, rather than merely being rejected by the fake service.
        state = {'check': {'conclusion': 'success'}, 'status': 'clean',
                 'reactions': ['+1', 'heart']}
        (root / 'state').write_text(json.dumps(state))
        stub = root / 'gh'
        stub.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['STUB_ROOT'])
args = sys.argv[1:]
with (root/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
if '--method' in args or '-X' in args:
    (root/'state').write_text('{}')
if os.environ['FAIL'] == '1': sys.exit(1)
print(os.environ['PERMISSION'])
''')
        stub.chmod(0o755)
        env = {'PATH': str(root) + os.pathsep + os.environ['PATH'],
               'STUB_ROOT': str(root), 'REPO': w.REPO, 'COMMENTER': login,
               'COMMENTER_ID': user_id, 'GITHUB_ACTOR': 'rerun-admin',
               'PERMISSION': permission if isinstance(permission, str) else json.dumps(permission),
               'FAIL': str(int(fail))}
        result = subprocess.run(['bash', '-eo', 'pipefail', '-c', script], env=env,
                                capture_output=True, text=True)
        calls = [json.loads(s) for s in (root/'calls').read_text().splitlines()] if (root/'calls').exists() else []
        return result, calls, json.loads((root/'state').read_text()), state


class RequesterPermissionTests(unittest.TestCase):
    def test_verified_original_author_write_and_admin_in_every_phase(self):
        for job in JOBS:
            for level in ('write', 'admin'):
                for login in ('comment-author', 'reviewer_acme'):
                    with self.subTest(job=job, level=level, login=login):
                        result, calls, after, before = probe(
                            {'permission': level, 'user': {'login': login, 'id': 17}}, job, login)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(calls, [['api', f'repos/{w.REPO}/collaborators/{login}/permission']])
                        self.assertEqual(after, before)

    def test_denied_unknown_and_rerun_identity_cannot_mutate_prior_success(self):
        responses = [None, {}, [], 'not json']
        responses += [{'permission': p, 'user': {'login': 'comment-author', 'id': 17}}
                      for p in ('read', 'triage', 'none', 'maintain')]
        responses += [{'permission': 'write', 'user': u} for u in
                      (None, {}, {'login': 'rerun-admin', 'id': 18},
                       {'login': 'comment-author', 'id': 18},
                       {'login': 'comment-author', 'id': '17'},
                       {'login': 'comment-author', 'id': True})]
        for job in JOBS:
            for response in responses:
                with self.subTest(job=job, response=response):
                    result, calls, after, before = probe(response, job)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(after, before)
                    self.assertTrue(all(c[0] == 'api' and len(c) == 2 for c in calls))
            result, calls, after, before = probe({}, job, fail=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(after, before)

    def test_revocation_is_observed_again_at_publication_even_without_helper(self):
        allowed = {'permission': 'write', 'user': {'login': 'comment-author', 'id': 17}}
        self.assertEqual(probe(allowed, 'start-check')[0].returncode, 0)
        revoked = copy.deepcopy(allowed)
        revoked['permission'] = 'read'
        result, _, after, before = probe(revoked, 'publish-status')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(after, before)
        steps = w.steps(w.job(w.MANUAL, 'publish-status'))
        condition = w.scalar(steps['Record failure without review tooling'], 'if', 8)
        for permission in ('failure', 'skipped', '', 'success'):
            for tooling in ('failure', 'success'):
                context = {'steps': {'requester-permission': {'outcome': permission},
                                     'tooling-ready': {'outcome': tooling}},
                           'needs': {'start-check': {'outputs': {'tooling_unavailable': 'true'}}}}
                self.assertEqual(bool(w.Evaluator(condition, context).evaluate()), permission == 'success')

    def test_gates_precede_writes_and_model_and_do_not_restrict_forks(self):
        for job in JOBS:
            steps = w.steps(w.job(w.MANUAL, job))
            if job != 'request':
                self.assertEqual(next(iter(steps)), NAME)
            gate = steps[NAME]
            source = '\n'.join(gate)
            self.assertIn('github.event.comment.user.login', source)
            self.assertIn('github.event.comment.user.id', source)
            self.assertNotIn('github.actor', source)
            self.assertNotIn('github.triggering_actor', source)
            self.assertNotIn('continue-on-error', source)
            self.assertNotIn('is_fork', source)
            if job == 'request':
                self.assertEqual(w.scalar(gate, 'if', 8), "steps.request.outputs.classification == 'valid'")
            else:
                self.assertNotIn('        if:', source)
        scripts = [w.run_block(w.steps(w.job(w.MANUAL, j))[NAME]) for j in JOBS]
        self.assertTrue(all(s == scripts[0] for s in scripts))
        # Both normal and emergency publication are downstream of the gate;
        # normal publication uses Actions' implicit success() condition.
        steps = w.steps(w.job(w.MANUAL, 'publish-status'))
        normal = '\n'.join(steps['Publish Claude Review result'])
        self.assertNotIn('always()', normal)
        self.assertNotIn('continue-on-error', '\n'.join(steps[NAME]))

    def test_invalid_original_identity_never_queries_api(self):
        for login, identity in [('bad/login', '17'), ('a'*40, '17'), ('comment-author', ''),
                                ('comment-author', '0'), ('comment-author', 'true')]:
            result, calls, after, before = probe({}, login=login, user_id=identity)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, [])
            self.assertEqual(after, before)


class PublicationDispatchTests(unittest.TestCase):
    """Select the real writers using workflow conditions and Actions success gating."""

    def dispatch(self, permission, tooling):
        import test_claude_review_check as f
        # Existing successful state is compared byte-for-byte on rejection.
        initial = {'check': {'conclusion': 'success'}, 'status': 'clean',
                   'reactions': ['+1', 'heart']}
        state = copy.deepcopy(initial)
        gate, _, _, _ = probe(permission, 'publish-status')
        outcome = 'success' if gate.returncode == 0 else 'failure'
        steps = w.steps(w.job(w.MANUAL, 'publish-status'))
        context = {'steps': {'requester-permission': {'outcome': outcome},
                             'tooling-ready': {'outcome': tooling}},
                   'needs': {'start-check': {'outputs': {'tooling_unavailable': 'false'}}}}
        writes = []
        executed = []
        for name in ('Publish Claude Review result', 'Record failure without review tooling'):
            condition = w.scalar(steps[name], 'if', 8)
            # Actions adds success() to conditions without a status function.
            selected = bool(w.Evaluator(condition, context).evaluate())
            if 'always()' not in condition:
                selected = selected and outcome == 'success'
            if not selected:
                continue
            executed.append(name)
            if name == 'Record failure without review tooling':
                result, calls = w.ToolingFallbackScriptTests().run_fallback(
                    w.MANUAL, PR_NUMBER='7', STATUS_COMMENT_ID='55')
                writes += [c for c in calls if c['method'] != 'GET']
            else:
                harness = f.ScriptTestCase()
                harness.setUp()
                try:
                    result = harness.run_script(['finalize'], [
                        f.history_rule(f.current_run_only()), f.comments_rule(f.ok(f.pages([]))),
                        f.patch_rule(f.ok('{}'))],
                        step_script=w.scalar(steps[name], 'run', 8), REVIEW_HELPER=str(f.SCRIPT),
                        CHECK_RUN_ID='99', START_RESULT='success', REVIEW_RESULT='failure',
                        ACTION_CONCLUSION='failure')
                    writes += harness.calls('PATCH') + harness.calls('POST') + harness.calls('DELETE')
                finally:
                    harness.tearDown()
            self.assertEqual(result.returncode, 1 if name == 'Record failure without review tooling' else 0,
                             result.stdout + result.stderr)
        for call in writes:
            path = call.get('path', ' '.join(call.get('args', [])))
            if '/check-runs/' in path:
                state['check'] = call['body']
            elif '/issues/comments/' in path:
                state['status'] = call['body']
            elif '/reactions' in path:
                state['reactions'] = call['body']
        return initial, state, writes, executed

    def test_denied_unavailable_and_revoked_permission_preserves_all_state(self):
        allowed = {'permission': 'write', 'user': {'login': 'comment-author', 'id': 17}}
        # Permission was granted at start; publication must not borrow it.
        self.assertEqual(probe(allowed, 'start-check')[0].returncode, 0)
        for response in (None, {}, 'unavailable',
                         {'permission': 'read', 'user': allowed['user']},
                         {'permission': 'admin', 'user': {'login': 'rerun-admin', 'id': 18}}):
            for tooling in ('success', 'failure'):
                with self.subTest(response=response, tooling=tooling):
                    initial, state, writes, executed = self.dispatch(response, tooling)
                    self.assertEqual(state, initial)
                    self.assertEqual(writes, [])
                    self.assertEqual(executed, [])

    def test_allowed_positive_controls_execute_real_normal_and_emergency_writers(self):
        allowed = {'permission': 'write', 'user': {'login': 'comment-author', 'id': 17}}
        for tooling, expected in (('success', 'Publish Claude Review result'),
                                  ('failure', 'Record failure without review tooling')):
            with self.subTest(tooling=tooling):
                initial, state, writes, executed = self.dispatch(allowed, tooling)
                self.assertEqual(executed, [expected])
                self.assertTrue(writes)
                self.assertNotEqual(state['check'], initial['check'])
                self.assertEqual(state['check']['conclusion'], 'failure')
