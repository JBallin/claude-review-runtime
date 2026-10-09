"""Offline authentication contract for the experimental API-key branch."""

import os
import subprocess
import unittest

import test_claude_review_workflows as w


class ApiKeyContractTests(unittest.TestCase):
    def test_only_api_key_is_accepted_and_passed_to_model(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            with self.subTest(workflow=path.name):
                text = path.read_text()
                contract = w.block(w.block(w.lines_of(path), 'on', 0), 'workflow_call', 2)
                self.assertEqual(w.block(contract, 'secrets', 4), [
                    '      ANTHROPIC_API_KEY:', '        required: true', ''])
                self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', text)
                self.assertNotIn('claude_code_oauth_token:', text)
                self.assertNotIn('secrets: inherit', text)
                review_jobs = [name for name in ('claude', 'review')
                               if f'  {name}:' in text]
                self.assertEqual(len(review_jobs), 1)
                model_steps = w.steps(w.job(path, review_jobs[0]))
                action = '\n'.join(model_steps['Review pull request'])
                self.assertIn('anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}', action)
                self.assertIn('classify_inline_comments: "false"', action)
                for override in ('github_token:', 'use_oidc:', 'app_id:', 'app_private_key:'):
                    self.assertNotIn(override, action)
                self.assertLess(list(model_steps).index('Validate model authentication'),
                                list(model_steps).index('Review pull request'))
                # Credentials go only to the guard and model input, not publishers.
                self.assertEqual(text.count('${{ secrets.ANTHROPIC_API_KEY }}'), 2)
                for name in ('start-check', 'publish-status'):
                    self.assertNotIn('ANTHROPIC_API_KEY', '\n'.join(w.job(path, name)))

    def test_missing_key_fails_with_fixed_diagnostic_and_values_are_not_logged(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            text = path.read_text()
            name = 'claude' if '  claude:' in text else 'review'
            guard = w.steps(w.job(path, name))['Validate model authentication']
            self.assertIn('          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}', guard)
            script = w.run_block(guard)
            for value in (None, '', 'offline-placeholder', '$(echo injected) `echo injected`'):
                with self.subTest(workflow=path.name, configured=bool(value)):
                    env = {'PATH': os.environ['PATH']}
                    if value is not None:
                        env['ANTHROPIC_API_KEY'] = value
                    result = subprocess.run(['bash', '-eo', 'pipefail', '-c', script],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0 if value else 1)
                    expected = '' if value else (
                        '::error::ANTHROPIC_API_KEY is required by the experimental api-key runtime.\n')
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, '')

    def test_consumer_examples_match_variant_while_installed_callers_remain_oauth(self):
        root = w.WORKFLOWS.parent.parent
        guide = (root / 'docs/consumer-workflows.md').read_text()
        self.assertEqual(guide.count('ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}'), 2)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN: ${{', guide)
        self.assertNotIn('secrets:', w.STALE.read_text())
        for name in ('self-review-automatic.yml', 'self-review-manual.yml'):
            self.assertIn('CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}',
                          (w.WORKFLOWS / name).read_text())


if __name__ == '__main__':
    unittest.main()
