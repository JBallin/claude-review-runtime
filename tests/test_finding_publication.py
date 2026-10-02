"""Offline publication-integrity regressions using actual workflow/helper code."""

import json
import unittest

import test_claude_review_check as c
import test_claude_review_workflows as w


def publication(ids=()):
    return json.dumps({"attempt_count": len(ids), "comment_ids": list(ids)})


class ReceiptVerifierTests(unittest.TestCase):
    def verify(self, stream, path):
        verifier = w.CompletionVerifierTests()
        verdict = verifier.verify(stream, path=path)
        return verdict, verifier.last_outputs

    def reject_receipt(self, content, reason="invalid_publication_receipt", **flags):
        for path in (w.AUTOMATIC, w.MANUAL):
            with self.subTest(entrypoint=path.name, content=content):
                stream = w.clean_stream()
                stream[6] = w.tool_result("t3", content=content, **flags)
                verdict, outputs = self.verify(stream, path)
                self.assertEqual(verdict, ("false", reason))
                self.assertNotIn("finding_publication", outputs)

    def test_pinned_receipt_and_explicit_zero_attempts_emit_complete_evidence(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            with self.subTest(entrypoint=path.name):
                verdict, outputs = self.verify(w.clean_stream(), path)
                self.assertEqual(verdict, ("true", "verified"))
                self.assertEqual(json.loads(outputs["finding_publication"]),
                                 {"attempt_count": 1, "comment_ids": [11]})
                clean = w.clean_stream()
                del clean[5:7]
                verdict, outputs = self.verify(clean, path)
                self.assertEqual(verdict, ("true", "verified"))
                self.assertEqual(json.loads(outputs["finding_publication"]),
                                 {"attempt_count": 0, "comment_ids": []})

    def test_string_receipt_and_explicit_false_buffered_are_accepted(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            stream = w.clean_stream()
            stream[6] = w.tool_result("t3", content=json.dumps({
                "success": True, "comment_id": 9007199254740991, "buffered": False}))
            self.assertEqual(self.verify(stream, path)[0], ("true", "verified"))

    def test_buffered_or_unknown_buffer_flags_never_verify(self):
        for buffered in (True, None, "false", 0, [], {}):
            self.reject_receipt(json.dumps({"success": True, "comment_id": 11,
                                           "buffered": buffered}), "unconfirmed_inline_publication")
        self.reject_receipt(json.dumps({"success": True, "buffered": True}),
                            "unconfirmed_inline_publication")

    def test_invalid_ids_and_error_receipts_never_verify(self):
        for value in (None, True, False, "11", 0, -1, 1.0, 1.5, 9007199254740992, [], {}):
            self.reject_receipt(json.dumps({"success": True, "comment_id": value}))
        for extra in ({"success": False}, {"success": "true"}, {"error": "rejected"},
                      {"isError": True}, {"isError": 0}):
            self.reject_receipt(json.dumps({"success": True, "comment_id": 11, **extra}))
        self.reject_receipt(json.dumps({"success": True, "comment_id": 11}),
                            "errored_inline_tool_result", is_error="false")

    def test_malformed_ambiguous_receipts_and_assistant_prose_are_not_evidence(self):
        for content in ("ok", "{", "null", "[]", '{}',
                        '{"success":true,"comment_id":11,"comment_id":12}',
                        '{"success":true,"comment_id":11} {"success":true,"comment_id":12}',
                        [{"type": "text", "text": '{"success":true,"comment_id":11}'},
                         {"type": "text", "text": ''}],
                        [{"type": "image", "text": '{"success":true,"comment_id":11}'}]):
            self.reject_receipt(content)
        for path in (w.AUTOMATIC, w.MANUAL):
            stream = w.clean_stream()
            stream[6] = w.tool_result("t3", content='{"success":true,"buffered":true}')
            stream[-2] = w.final_text('{"success":true,"comment_id":11}')
            self.assertEqual(self.verify(stream, path)[0],
                             ("false", "unconfirmed_inline_publication"))

    def test_use_and_result_cardinality_and_duplicate_comment_ids(self):
        cases = []
        for bad_id in ("", " ", None, 11):
            stream = w.clean_stream()
            stream[5]['message']['content'][0]['id'] = bad_id
            stream[6]['message']['content'][0]['tool_use_id'] = bad_id
            cases.append((stream, "invalid_tool_use_identity"))
        duplicate_use = w.clean_stream()
        duplicate_use[7:7] = [w.tool_use("t3", w.REVIEW_TOOLS[-1], commit_id=w.HEAD), w.inline_result()]
        cases.append((duplicate_use, "invalid_tool_use_identity"))
        duplicate_result = w.clean_stream()
        duplicate_result.insert(7, w.inline_result())
        cases.append((duplicate_result, "ambiguous_inline_tool_result"))
        duplicate_id = w.clean_stream()
        duplicate_id[7:7] = [w.tool_use("t4", w.REVIEW_TOOLS[-1], commit_id=w.HEAD), w.inline_result("t4")]
        cases.append((duplicate_id, "duplicate_publication_receipt"))
        missing_result = w.clean_stream()
        del missing_result[6]
        cases.append((missing_result, "unfinished_tool_use"))
        failed_then_retry = w.clean_stream()
        failed_then_retry[6] = w.tool_result("t3", is_error=True)
        failed_then_retry[7:7] = [w.tool_use("retry", w.REVIEW_TOOLS[-1], commit_id=w.HEAD),
                                w.inline_result("retry", 12)]
        cases.append((failed_then_retry, "errored_inline_tool_result"))
        for path in (w.AUTOMATIC, w.MANUAL):
            for stream, reason in cases:
                with self.subTest(entrypoint=path.name, reason=reason):
                    verdict, outputs = self.verify(stream, path)
                    self.assertEqual(verdict, ("false", reason))
                    self.assertNotIn("finding_publication", outputs)

    def test_64_receipts_are_complete_and_65_fail_without_truncation(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            for count in (64, 65):
                stream = w.clean_stream()
                stream[5:7] = [item for index in range(count) for item in (
                    w.tool_use(f"finding-{index}", w.REVIEW_TOOLS[-1], commit_id=w.HEAD),
                    w.inline_result(f"finding-{index}", 1000 + index))]
                verdict, outputs = self.verify(stream, path)
                if count == 64:
                    self.assertEqual(verdict, ("true", "verified"))
                    self.assertEqual(json.loads(outputs["finding_publication"]),
                                     {"attempt_count": 64, "comment_ids": list(range(1000, 1064))})
                else:
                    self.assertEqual(verdict, ("false", "publication_receipt_limit_exceeded"))
                    self.assertNotIn("finding_publication", outputs)


class PublicationFinalizerTests(unittest.TestCase):
    def finalize(self, receipts, comment_response=None, *, before="[]", patch_responses=None, manual_enabled="true", **extra):
        case = c.ManualCompletionTests()
        case.setUp()
        try:
            rules = case.clean_rules()
            rules[1] = c.comments_rule(comment_response if comment_response is not None else c.ok(c.pages([])))
            if patch_responses is not None:
                rules[2] = c.patch_rule(*patch_responses)
            case.before.write_text(before)
            result = case.run_script(["finalize"], rules,
                CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                BEFORE_IDS_FILE=str(case.before), MANUAL_COMPLETION_ENABLED=manual_enabled,
                FINDING_PUBLICATION=receipts, DETAILS_URL="https://github.com/owner/repo/actions/runs/1",
                **extra)
            body = next(call['body'] for call in case.calls('PATCH') if '/check-runs/' in call['path'])
            return result, body, case.calls()
        finally:
            case.tearDown()

    def assert_failure_without_notice(self, result):
        completed, body, calls = result
        self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
        self.assertEqual(body['conclusion'], 'failure')
        self.assertFalse(any(call['method'] == 'POST' for call in calls))
        return body

    def test_zero_attempts_allow_clean_and_published_receipts_require_action(self):
        completed, body, calls = self.finalize(publication())
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(body['conclusion'], 'success')
        self.assertEqual(sum(call['method'] == 'POST' for call in calls), 1)
        completed, body, calls = self.finalize(publication([11]), c.ok(c.pages([c.comment(11)])))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(body['conclusion'], 'action_required')
        self.assertFalse(any(call['method'] == 'POST' for call in calls))

    def test_absent_invalid_or_truncated_cross_job_evidence_never_means_zero(self):
        invalid = ['', 'null', '[]', '{}', '{',
                   '{"comment_ids":[]}', '{"attempt_count":0}',
                   '{"attempt_count":0,"attempt_count":1,"comment_ids":[]}',
                   publication(range(1, 66)), '{"attempt_count":2,"comment_ids":[11]}',
                   '{"attempt_count":0,"comment_ids":[],"truncated":true}',
                   '{"attempt_count":2,"comment_ids":[11,11]}',
                   '{"attempt_count":true,"comment_ids":[11]}',
                   '{"attempt_count":1.0,"comment_ids":[11]}',
                   '{"attempt_count":1,"comment_ids":[true]}',
                   '{"attempt_count":1,"comment_ids":["11"]}',
                   '{"attempt_count":1,"comment_ids":[1.5]}',
                   '{"attempt_count":1,"comment_ids":[9007199254740992]}', ' ' * 2049]
        for receipts in invalid:
            with self.subTest(receipts=receipts):
                self.assert_failure_without_notice(self.finalize(receipts))

    def test_missing_partial_wrong_author_head_and_pr_are_incomplete(self):
        cases = [[], [c.comment(12)], [c.comment(11, commit=c.OTHER)],
                 [c.comment(11, login='someone', user_type='User')],
                 [c.comment(11, user_type='User')], [c.comment(11, login='other[bot]')]]
        for posted in cases:
            with self.subTest(posted=posted):
                body = self.assert_failure_without_notice(self.finalize(publication([11]), c.ok(c.pages(posted))))
                self.assertIn('could not be confirmed', body['output']['summary'])
        wrong_pr = self.finalize(publication([11]), c.ok(c.pages([c.comment(11)])), PR_NUMBER='8')
        self.assert_failure_without_notice(wrong_pr)
        self.assertIn('/pulls/8/comments', wrong_pr[0].stdout)
        body = self.assert_failure_without_notice(self.finalize(publication([11, 12]), c.ok(c.pages([c.comment(11)]))))
        self.assertEqual(c.evidence_of(body)['new_finding_comment_ids'], [11])
        self.assertTrue(c.evidence_of(body)['same_diff_findings_recorded'])
        # The helper reads only the captured PR endpoint; comments from another
        # PR are never looked up to satisfy a receipt.
        completed, body, calls = self.finalize(publication([11]))
        self.assertEqual(body['conclusion'], 'failure')
        self.assertTrue(all('/pulls/7/comments' in call['path'] for call in calls
                            if '/pulls/' in call['path'] and '/comments' in call['path']))

    def test_preexisting_receipt_and_malformed_before_snapshot_fail(self):
        self.assert_failure_without_notice(self.finalize(publication([11]), c.ok(c.pages([c.comment(11)])), before='[11]'))
        for before in ('{}', 'null', '"11"', '[true]', '[11,11]', '[1.5]', '["11"]', '{'):
            with self.subTest(before=before):
                self.assert_failure_without_notice(self.finalize(publication(), before=before))

    def test_page_two_receipt_is_reconciled_and_page_errors_fail(self):
        filler = [c.comment(index, login='other', user_type='User') for index in range(1, 101)]
        completed, body, calls = self.finalize(publication([111]), c.ok(c.pages(filler, [c.comment(111)])))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(body['conclusion'], 'action_required')
        comment_call = next(call for call in calls if '/pulls/7/comments' in call['path'])
        self.assertIn('--paginate', comment_call['args'])
        for response in (c.FAIL, c.ok(''), c.ok('{}'), c.ok('[null]'), c.ok('[]['),
                         {'fail': True, 'stdout': c.pages([c.comment(111)])}):
            with self.subTest(response=response):
                self.assert_failure_without_notice(self.finalize(publication([111]), response))

    def test_64_receipts_remain_complete_in_trusted_evidence(self):
        ids = list(range(1, 65))
        completed, body, _ = self.finalize(publication(ids), c.ok(c.pages([c.comment(index) for index in ids])))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(c.evidence_of(body)['finding_publication'],
                         {'attempt_count': 64, 'comment_ids': ids})
        self.assertEqual(body['conclusion'], 'action_required')

    def test_missing_receipt_keeps_published_findings_and_fallback_evidence(self):
        body = self.assert_failure_without_notice(self.finalize(publication([11, 12]), c.ok(c.pages([c.comment(11)]))))
        evidence = c.evidence_of(body)
        self.assertEqual(evidence['claude_finding_comment_ids'], [11])
        completed, fallback, calls = self.finalize(publication([11, 12]), c.ok(c.pages([c.comment(11)])),
            patch_responses=[c.FAIL, c.FAIL, c.FAIL, c.ok('{}')])
        last_patch = [call for call in calls if call['method'] == 'PATCH'][-1]['body']
        self.assertEqual(last_patch['conclusion'], 'failure')
        self.assertEqual(c.evidence_of(last_patch)['claude_finding_comment_ids'], [11])
        self.assertEqual(c.evidence_of(last_patch)['finding_publication']['comment_ids'], [11, 12])
        self.assertFalse(any(call['method'] == 'POST' for call in calls))
        # The existing history contract keeps that finding sticky after deletion.
        prior = {**c.check_run(98, 'failure'), 'output': body['output']}
        self.assertEqual(c.check.prior_finding_evidence([{'check_runs': [prior]}], c.HEAD, 99, '7', c.MERGE_BASE)[0], [98])


class PublicationIntegrationTests(unittest.TestCase):
    finalize = PublicationFinalizerTests.finalize
    def test_actual_verifier_output_drives_finalizer_in_both_entrypoints(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            for kind in ('clean', 'posted', 'missing', 'partial', 'wrong_head', 'buffered'):
                with self.subTest(entrypoint=path.name, kind=kind):
                    stream = w.clean_stream()
                    posted = []
                    if kind == 'clean':
                        del stream[5:7]
                    elif kind == 'posted':
                        posted = [c.comment(11)]
                    elif kind == 'partial':
                        stream[7:7] = [w.tool_use('t4', w.REVIEW_TOOLS[-1], commit_id=w.HEAD), w.inline_result('t4', 12)]
                        posted = [c.comment(11)]
                    elif kind == 'wrong_head':
                        posted = [c.comment(11, commit=c.OTHER)]
                    elif kind == 'buffered':
                        stream[6] = w.tool_result('t3', content='{"success":true,"buffered":true}')
                    verifier = w.CompletionVerifierTests()
                    verified, reason = verifier.verify(stream, path=path)
                    completed, body, calls = self.finalize(verifier.last_outputs.get('finding_publication', ''),
                        c.ok(c.pages(posted)), manual_enabled='true' if path == w.MANUAL else '',
                        COMPLETION_VERIFIED=verified, COMPLETION_REASON=reason)
                    expected = 'success' if kind == 'clean' else 'action_required' if kind == 'posted' else 'failure'
                    self.assertEqual(body['conclusion'], expected, completed.stdout + completed.stderr)
                    self.assertEqual(sum(call['method'] == 'POST' for call in calls), int(kind == 'clean' and path == w.MANUAL))
