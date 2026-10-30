"""Offline gallery safety and renderer-reuse contracts."""

from contextlib import redirect_stdout
import importlib.util
import io
import os
import re
from itertools import groupby
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_presentation_gallery.py"
spec = importlib.util.spec_from_file_location("presentation_gallery", SCRIPT)
gallery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gallery)


def sample_items():
    return [(case.case_id, case.body) for case in gallery.gallery_samples()]


class PresentationGalleryTests(unittest.TestCase):
    def test_deterministic_complete_gallery(self):
        body = gallery.render_gallery()
        self.assertEqual(body, gallery.render_gallery())
        self.assertTrue(body.startswith("# " + gallery.HEADING + "\n"))
        self.assertNotIn("Source revision", body)
        self.assertNotIn("Pending —", body)
        self.assertNotIn("Visual-QA observations", body)
        self.assertEqual(len(gallery.gallery_samples()), 63)
        self.assertEqual([int(number) for number in re.findall(r"^### ([0-9]+) — ", body, re.MULTILINE)],
                         list(range(1, 64)))
        # This committed audit file is not posted as one GitHub comment.
        self.assertLess(len(body.encode("utf-8")), 200_000)

    def test_semantic_anchors_are_unique_and_independent_of_group_order(self):
        samples = gallery.gallery_samples()
        case_ids = [case.case_id for case in samples]
        self.assertEqual(len(case_ids), len(set(case_ids)))
        for case in samples:
            self.assertRegex(case.case_id, r"^[a-z][a-z0-9-]+$")
            self.assertNotRegex(case.title, r"^(?:[A-Z]+[0-9]*|[0-9]+) — ")
        self.assertEqual(re.findall(r'^<a id="([^"]+)"></a>$', gallery.render_gallery(), re.MULTILINE),
                         case_ids)
        with mock.patch.object(gallery, "GROUP_ORDER", tuple(reversed(gallery.GROUP_ORDER))):
            reordered = gallery.gallery_samples()
        self.assertNotEqual([case.case_id for case in reordered], case_ids)
        self.assertEqual({case.case_id: (case.title, case.body) for case in reordered},
                         {case.case_id: (case.title, case.body) for case in samples})

    def test_insertion_and_reordering_renumber_headings_without_changing_case_sections(self):
        samples = gallery.gallery_samples()
        inserted = gallery.GallerySample("additional-case", "incomplete", "Additional sample", samples[0].body)
        changed_order = [inserted, *reversed(samples)]
        original = gallery.render_gallery()
        with mock.patch.object(gallery, "gallery_samples", return_value=changed_order):
            changed = gallery.render_gallery()
        self.assertEqual([int(number) for number in re.findall(r"^### ([0-9]+) — ", changed, re.MULTILINE)],
                         list(range(1, 65)))
        self.assertEqual(re.findall(r'^<a id="([^"]+)"></a>$', changed, re.MULTILINE),
                         [case.case_id for case in changed_order])

        def case_sections(body):
            # Ignore the display heading while retaining each stable anchor's
            # exact rendered sample and optional inline finding context.
            return {re.match(r'<a id="([^"]+)">', section)[1]: section.split("\n\n", 2)[2]
                    for section in body.removesuffix("\n").split("\n\n---\n\n")[1:]}

        changed_sections = case_sections(changed)
        for case_id, section in case_sections(original).items():
            self.assertEqual(changed_sections[case_id], section)

    def test_related_cases_are_contiguous_in_reading_order(self):
        samples = gallery.gallery_samples()
        self.assertEqual(tuple(group for group, _ in groupby(case.group for case in samples)),
                         gallery.GROUP_ORDER)
        case_ids = [case.case_id for case in samples]
        for related in (
                ["incomplete-unknown", "inline-tool-partial", "inline-tool-zero",
                 "inline-tool-unknown-count", "inline-tool-stale", "unread-automatic", "unread-manual",
                 "workflow-changed", "workflow-changed-superseded"],
                ["running-history-clean", "running-history-findings", "running-history-manual"],
                ["incomplete-history-clean", "incomplete-history-findings", "incomplete-history-manual"],
                ["publication-incomplete-none", "publication-incomplete-success",
                 "publication-incomplete-action-required", "publication-incomplete-manual"]):
            start = case_ids.index(related[0])
            self.assertEqual(case_ids[start:start + len(related)], related)

    def test_each_sample_preserves_helper_metadata_and_normalizes_hard_breaks(self):
        check = gallery.check
        with mock.patch.object(check, "status_comment_body", wraps=check.status_comment_body) as status:
            with mock.patch.object(check, "completion_comment_body", wraps=check.completion_comment_body) as completion:
                samples = gallery.gallery_samples()
        self.assertEqual(status.call_count, 58)
        self.assertEqual(completion.call_count, 5)
        self.assertEqual([call.kwargs.get("observed_base_sha") for call in completion.call_args_list],
                         [gallery.fixtures.BASE_TIP, gallery.fixtures.BASE_TIP, gallery.fixtures.BASE_TIP, gallery.fixtures.OTHER, None])
        body = gallery.render_gallery()
        for case in samples:
            case_id, raw = case.case_id, case.body
            with self.subTest(case_id=case_id):
                self.assertIn(gallery.sample_markdown(raw), body)
                self.assertTrue("claude-review-runtime:" in raw)

    def test_selected_model_views_distinguish_primary_evidence_from_usage(self):
        cases = dict(sample_items())
        for case_id in ('selection-running', 'opus-observed', 'opus-multiple', 'selection-receipt'):
            for hidden in ('', '<!--\nAuxiliary Haiku model evidence\n-->\n'):
                with self.subTest(case_id=case_id, multiline_comment=bool(hidden)):
                    main, details = (hidden + cases[case_id]).split('<details>', 1)
                    visible = re.sub(r'<!--.*?-->', '', main, flags=re.DOTALL)
                    self.assertNotIn('**Model:**', visible)
                    self.assertNotIn('**Effort:**', visible)
                    expected_model = 'Opus' if case_id == 'selection-running' else 'Opus 5.5'
                    self.assertIn('**Model:** ' + expected_model + ' · **Effort:** High', details)
                    visible_details = re.sub(r'<!--.*?-->', '', details, flags=re.DOTALL)
                    self.assertNotIn('Haiku', visible_details)
                    self.assertNotIn('evidence', visible_details)
        for case_id, exception in (('opus-mismatch', 'primary model mismatch'),
                                   ('selection-receipt-mismatch', 'primary model mismatch'),
                                   ('opus-unknown', 'primary model evidence missing'),
                                   ('opus-legacy', 'primary model evidence missing'),
                                   ('opus-ambiguous', 'primary model evidence ambiguous')):
            visible = re.sub(r'<!--.*?-->', '', cases[case_id].split('<details>')[0], flags=re.DOTALL)
            self.assertIn('⚠️ **Model:** Opus selected; ' + exception, visible)
        mixed = gallery.check.current_selection(
            gallery.fixtures.presentation_owner(kind='issue_comment', target=12, started=gallery.STARTED),
            {'body': cases['opus-multiple']})
        self.assertEqual(mixed['models'], ['claude-haiku-4-5', 'claude-opus-5-5'])
        self.assertEqual(mixed['primary_model_evidence']['assistant'], ['claude-opus-5-5'])
        self.assertIsNone(mixed['observed_effort'])
        receipt = cases['selection-receipt'].split('<details>')[0]
        self.assertIn('**Reviewed commit:** ' + gallery.check.commit_link(gallery.fixtures.HEAD,
                      gallery.fixtures.REPO) + '\n\n', receipt)
        self.assertNotIn('**Model:**', receipt)
        self.assertIn('**Model:** Opus 5.5 · **Effort:** High', cases['selection-receipt'].split('<details>')[1])

    def test_repository_configuration_views_keep_known_fields_inside_details(self):
        cases = dict(sample_items())
        for case_id, line in (
                ('repository-default-running', '**Model:** Sonnet · **Effort:** Medium'),
                ('repository-default-observed', '**Model:** Sonnet 5.5 · **Effort:** Medium'),
                ('inherited-model-observed', '**Model:** Haiku 5.5'),
                ('manual-partial-default', '**Model:** Haiku 5.5 · **Effort:** Medium')):
            with self.subTest(case_id=case_id):
                main, details = cases[case_id].split('<details>', 1)
                main = re.sub(r'<!--.*?-->', '', main, flags=re.DOTALL)
                self.assertNotIn('**Model:**', main)
                self.assertNotIn('**Effort:**', main)
                self.assertNotIn('⚠️', main)
                self.assertIn(line, details)
                if case_id == 'inherited-model-observed':
                    self.assertNotIn('**Effort:**', details)
        main, details = cases['repository-invalid-setting'].split('<details>', 1)
        self.assertIn('⚠️ **Review configuration:** Invalid model setting.', main)
        self.assertNotIn('primary model evidence missing', main)
        self.assertIn('**Effort:** High', details)
        self.assertNotIn('**Model:**', details)

    def test_inherited_model_conflict_stays_in_receipts_without_a_visible_model_warning(self):
        raw = dict(sample_items())['inherited-model-ambiguous']
        main, details = raw.split('<details>', 1)
        visible = re.sub(r'<!--.*?-->', '', main, flags=re.DOTALL)
        self.assertNotIn('**Model:**', visible + details)
        self.assertNotIn('primary model evidence ambiguous', visible)
        self.assertNotIn('**Effort:**', visible)
        self.assertIn('**Effort:** High', details)
        with mock.patch.dict(os.environ, {}, clear=True):
            comment = {'body': raw, 'user': {'login': 'github-actions[bot]', 'type': 'Bot'}}
            record = gallery.check.current_selection(gallery.check.status_owner(comment), comment)
        self.assertEqual(record['primary_model_evidence']['assistant'],
                         ['claude-opus-5-5', 'claude-sonnet-5-5'])

    def test_current_and_previous_settings_remain_bound_to_their_generations(self):
        raw = dict(sample_items())['running-history-clean']
        details = raw.split('<details>', 1)[1]
        current, previous = details.split('### Previous completed review')
        self.assertIn('**Model:** Opus · **Effort:** High', current)
        self.assertNotIn('Sonnet', current)
        self.assertIn('**Model:** Sonnet 5.5 · **Effort:** Medium', previous)
        self.assertNotIn('**Model:** Opus', previous)
        check = gallery.check
        comment = {'body': raw, 'user': {'login': 'github-actions[bot]', 'type': 'Bot'}}
        with mock.patch.dict(os.environ, {}, clear=True):
            owner = check.status_owner(comment)
            current_record = check.current_selection(owner, comment)
            historical = check.last_completed_review_details(comment)['selection']
        self.assertEqual(current_record['generation'], owner['generation'])
        self.assertNotEqual(historical['generation'], owner['generation'])
        self.assertEqual(historical['configuration']['requested_model'], 'sonnet')

    def test_current_findings_completion_has_current_commit_run_and_evidence(self):
        check = gallery.check
        with mock.patch.object(check, "status_comment_body", wraps=check.status_comment_body) as status:
            samples = gallery.gallery_samples()
        case = next(case for case in samples if case.case_id == gallery.FINDINGS_SAMPLE)
        raw = case.body
        call = next(call for call in status.call_args_list
                    if call.args == (gallery.fixtures.HEAD, "main", "action_required"))
        self.assertEqual(call.args, (gallery.fixtures.HEAD, "main", "action_required"))
        self.assertNotIn("last_review", call.kwargs)
        visible, details = raw.split("<details>", 1)
        self.assertIn("⚠️ **Findings**", visible)
        self.assertIn("| Reviewed commit |", visible)
        self.assertIn(check.commit_link(gallery.fixtures.HEAD, gallery.fixtures.REPO), visible)
        self.assertIn("PR opened for review", visible)
        self.assertIn(gallery.COMPLETED_AT, visible)
        self.assertNotIn("Assess the recorded findings in the inline review threads.", visible)
        self.assertNotIn("No findings", raw)
        self.assertNotIn("Previous completed review", raw)
        self.assertEqual(check.status_state({"body": raw}), "action_required")
        self.assertEqual(check.last_completed_review({"body": raw}),
                         (gallery.fixtures.HEAD, "main", "action_required"))
        self.assertEqual(check.status_owner({"body": raw})["run"], 5)
        self.assertIn("**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** "
                      + check.commit_link(gallery.fixtures.BASE_TIP, gallery.fixtures.REPO), details)
        body = gallery.render_gallery()
        self.assertIn(gallery.sample_markdown(raw), body)
        self.assertIn("Synthetic inline finding context (separate from the status comment)", body)
        self.assertIn(gallery.SYNTHETIC_FINDING, body)
        self.assertIn("src/cache.py:42", gallery.SYNTHETIC_FINDING)
        self.assertIn("[P2]", gallery.SYNTHETIC_FINDING)

    def test_raw_gallery_retains_complete_synthetic_metadata(self):
        body = gallery.render_gallery()
        self.assertIn(gallery.check.STATUS_MARKER, body)
        self.assertIn(gallery.check.COMPLETION_PREFIX, body)
        for case in gallery.gallery_samples():
            self.assertIn(gallery.sample_markdown(case.body), body)
        self.assertEqual(gallery.gallery_samples()[0].case_id, "current-clean")
        self.assertNotIn("@claude", body)
        self.assertNotIn("@codex", body)

    def test_inline_failure_samples_preserve_attempt_and_hide_receipts(self):
        samples = dict(sample_items())
        for label, count in (("partial", 1), ("zero", 0), ("unknown-count", None), ("stale", 1)):
            body = samples["inline-tool-" + label]
            self.assertIn(gallery.check.inline_failure_explanation(count), body)
            self.assertIn("/pull/7/checks?check_run_id=99", body)
            self.assertIn(gallery.check.INCOMPLETE_RECOVERY, body)
            self.assertEqual("request an approved fresh" in body, label != "stale")
            self.assertIn(gallery.check.INLINE_FAILURE_PREFIX, body)
            self.assertNotIn(gallery.check.INLINE_FAILURE_PREFIX, gallery.strip_operational_markers(body))

    def test_strip_only_operational_comments_preserves_visible_copy_and_reason(self):
        reason = "<!--\n**Reason:** " + gallery.check.STATUS_REASONS["base_advanced"] + "\n-->"
        expected = "## Heading\n\n" + reason + "\nvisible\n<!-- unrelated -->"
        raw = gallery.check.STATUS_MARKER + "\n" + expected
        self.assertEqual(gallery.strip_operational_markers(raw), expected)

    def test_hard_break_normalization_preserves_lines_without_trailing_whitespace(self):
        raw = gallery.check.STATUS_MARKER + "\nfirst  \nsecond\n"
        self.assertEqual(gallery.sample_markdown(raw), gallery.check.STATUS_MARKER + "\nfirst\\\nsecond\n")
        self.assertTrue(all(line == line.rstrip() for line in gallery.render_gallery().splitlines()))

    def test_real_samples_keep_legacy_reason_hidden_and_label_identity_roles(self):
        samples = dict(sample_items())
        for case_id, raw in samples.items():
            if case_id in ("baseline-clean", "baseline-findings"):
                with self.subTest(case_id=case_id):
                    body = gallery.strip_operational_markers(raw)
                    receipt = "<!--\n**Reason:** " + gallery.check.STATUS_REASONS["base_advanced"] + "\n-->"
                    self.assertIn(receipt, body)
                    self.assertIn("**Current target commit:**", body)
                    self.assertIn("| Reviewed commit |", body)
                    self.assertEqual(body.count(
                        "⚠️ The target branch differs from the reviewed baseline. Integration with its current version hasn’t been reviewed."
                    ), 1)
            elif case_id == "incomplete-unknown":
                self.assertIn("| Attempt commit |", raw)
            elif case_id == "compact-clean":
                self.assertNotIn("The observed target differed from the reviewed baseline.", raw)
                self.assertIn("Claude Review: No findings. 🎉", raw)
                self.assertIn("**Reviewed commit:**", raw)
                self.assertNotIn(gallery.check.TARGET_TIP_WARNING, raw)
                self.assertNotIn("target commit:**", raw)
                self.assertNotIn(gallery.check.CAPTURED_PATCH_SCOPE, raw)
                self.assertNotIn("This is not merge approval.", raw)
                self.assertIn("**Runtime used:** [`JBallin/claude-review-runtime@fffffff`]", raw.split("<details>")[1])
            elif case_id == "compact-differing-baseline":
                visible, details = raw.split("<details>", 1)
                self.assertNotIn("The observed target differed", visible)
                self.assertEqual(details.count("The observed target differed from the reviewed baseline."), 1)
                self.assertIn("Claude Review: No findings. 🎉", raw)
                self.assertNotIn(gallery.check.TARGET_TIP_WARNING, raw)
                self.assertNotIn(gallery.check.CAPTURED_PATCH_SCOPE, raw)
                self.assertNotIn("This is not merge approval.", raw)
                self.assertIn("**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** " + gallery.check.commit_link(gallery.fixtures.BASE_TIP, gallery.fixtures.REPO), raw)
                self.assertIn("**Observed target commit:** " + gallery.check.commit_link(gallery.fixtures.OTHER, gallery.fixtures.REPO), raw)
                self.assertNotIn("**Current target commit:**", raw)

    def test_pin_recovery_is_explicitly_synthetic_and_separate_from_generic_receipts(self):
        cases = dict(sample_items())
        recovery = cases["manual-pin-recovery"]
        headline, details = recovery.split("<details>", 1)
        self.assertIn("Claude Review: No findings. 🎉", headline)
        self.assertNotIn("Synthetic recovery context", headline)
        self.assertNotIn("proposed pin", headline)
        runtime_line = next(line for line in details.splitlines() if line.startswith("**Runtime used:**"))
        self.assertIn(runtime_line + "\nThis review used the deployed Runtime, not the proposed pin.", details)
        self.assertTrue(runtime_line.endswith("  "))
        self.assertNotIn("from `fffffff`", details)
        self.assertIn("**Runtime used:** [`JBallin/claude-review-runtime@fffffff`]", recovery)
        for key in ("compact-clean", "compact-differing-baseline"):
            self.assertNotIn("deployed Runtime", cases[key])
            self.assertNotIn("proposed pin", cases[key])
        for key, body in cases.items():
            if not key.startswith("legacy-"):
                self.assertIn("**Runtime used:**", body, key)
        self.assertNotIn("owner/runtime", gallery.render_gallery())

    def test_publication_failures_and_unavailable_check_variants_use_current_attempt(self):
        groups = {group: [(case.case_id, case.body) for case in gallery.gallery_samples() if case.group == group]
                  for group in ("publication-incomplete", "publication-unavailable", "running-unavailable")}
        for group, cases in groups.items():
            self.assertEqual(len(cases), 4 if group == "publication-incomplete" else 3)
            for case_id, body in cases:
                with self.subTest(case_id=case_id):
                    visible, details = body.split("<details>", 1)
                    self.assertIn("| Attempt commit |", visible)
                    self.assertIn("| " + gallery.check.commit_link(gallery.fixtures.OTHER, gallery.fixtures.REPO)
                                  + (" | Manual request |" if case_id.endswith("-manual") else " | Draft marked ready |"), visible)
                    self.assertNotIn("✅ **Completed**", visible)
                    self.assertNotIn("⚠️ **Findings**", visible)
                    self.assertIn("🔄 **Running**" if group == "running-unavailable" else "❌ **Incomplete**", visible)
                    if group == "publication-incomplete":
                        self.assertIn("The authoritative Check result could not be published.", visible)
                    else:
                        self.assertIn("authoritative Check publication is unavailable", visible)
                    if case_id.endswith("-none"):
                        self.assertNotIn("### Previous completed review", details)
                    else:
                        self.assertIn("### Current attempt", details)
                        self.assertIn("\n\n### Previous completed review\n", details)
                        self.assertIn("**Result:** " + ("Findings." if case_id.endswith("-action-required") else "No findings."), details)
                        self.assertEqual(gallery.check.last_completed_review({"body": body}),
                                         (gallery.fixtures.HEAD, "main", "action_required" if case_id.endswith("-action-required") else "success"))

    def test_manual_history_samples_attach_scope_only_to_previous_review(self):
        cases = [(case.case_id, case.body) for case in gallery.gallery_samples()
                 if case.group in ("running-history", "incomplete-history", "publication-incomplete")
                 and case.case_id.endswith("-manual")]
        self.assertEqual(len(cases), 3)
        for case_id, body in cases:
            with self.subTest(case_id=case_id):
                current, previous = body.split("### Previous completed review")
                self.assertNotIn(gallery.check.CAPTURED_PATCH_SCOPE, current)
                self.assertNotIn(gallery.check.CAPTURED_PATCH_SCOPE, previous)
                self.assertIn("**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:**", previous)
                self.assertIn("**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:**", current)

    def test_compact_metadata_keeps_links_on_consecutive_rendered_lines(self):
        for case_id, body in sample_items():
            if case_id in ("current-clean", "baseline-clean", "baseline-findings", "compact-clean",
                           "compact-differing-baseline", "retargeted-clean", "retargeted-findings"):
                with self.subTest(case_id=case_id):
                    details = body.split("<details>")[1]
                    self.assertNotIn("\n\n[", details)
                    self.assertIn("**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:**", details)
                    if case_id in ("current-clean", "baseline-clean"):
                        self.assertNotIn("**Result:** No findings.", details)
                    if case_id in ("compact-clean", "compact-differing-baseline"):
                        self.assertNotIn("captured patch only", body)
                        self.assertNotIn("merge approval", body)

    def test_run_links_are_embedded_in_the_correct_sample_context(self):
        reviewed = "https://github.com/owner/repo/actions/runs/5"
        attempt = "https://github.com/owner/repo/actions/runs/6"
        for case_id, body in sample_items():
            with self.subTest(case_id=case_id):
                details = body.split("<details>")[1]
                self.assertNotIn("workflow run]", details)
                self.assertNotIn("[Current attempt]", details)
                self.assertNotIn("[Previous completed review]", details)
                self.assertNotIn("[Review run]", details)
                if case_id.startswith(("shared-run-", "legacy-history-", "stale-")):
                    continue
                if "### Previous completed review" in details:
                    current, previous = details.split("### Previous completed review")
                    self.assertIn("### Current attempt", current)
                    self.assertIn(f"**[Attempt]({attempt}) baseline:**", current)
                    self.assertIn(f"**[Reviewed]({reviewed}) against:**", previous)
                    self.assertNotIn(reviewed, current)
                    self.assertNotIn(attempt, previous)
                elif case_id.startswith("inline-tool-") or case_id in ("selection-running", "selection-incomplete", "selection-tooling-unavailable", "repository-default-running", "repository-invalid-setting"):
                    self.assertIn(f"**[Attempt]({reviewed}) baseline:**", details)
                    self.assertNotIn("[Reviewed]", details)
                elif case_id.startswith(("publication-incomplete-", "publication-unavailable-", "unread-", "workflow-changed")) or case_id == "incomplete-unknown":
                    url = reviewed if case_id in ("incomplete-unknown", "unread-automatic",
                                                   "workflow-changed", "workflow-changed-superseded") else attempt
                    self.assertIn(f"**[Attempt]({url}) baseline:**", details)
                    self.assertNotIn("[Reviewed]", details)
                elif case_id.startswith("running-unavailable-"):
                    self.assertIn(f"**[Attempt]({attempt}) baseline:**", details)
                    self.assertNotIn("[Reviewed]", details)
                else:
                    self.assertIn(f"**[Reviewed]({reviewed}) against:**", details)

    def test_incomplete_explanations_are_visible_and_keep_diagnostics_collapsed(self):
        for case_id, body in sample_items():
            if case_id not in ("unread-automatic", "unread-manual", "workflow-changed", "workflow-changed-superseded"):
                continue
            visible, details = body.split("<details>", 1)
            with self.subTest(case_id=case_id):
                self.assertIn("❌ **Incomplete**", visible)
                self.assertIn("recovery guidance]", visible.lower())
                self.assertIn("**Reason:**", details)
                self.assertNotIn("**Reason:**", visible)
                self.assertNotIn("**Note:**", body)
                if case_id in ("unread-automatic", "unread-manual"):
                    self.assertIn("Required review inputs were not fully read.", visible)
                    self.assertIn("#incomplete-captured-input-reads", visible)
                    self.assertNotIn("validation rejection", body)
                else:
                    self.assertIn("likely caused this incomplete review", visible)
                    self.assertIn("to confirm.", visible)
                    self.assertIn("/actions/runs/5/attempts/1", visible)
                    self.assertIn("cause of the incomplete review is unknown", details)

    def test_edge_samples_keep_plain_headings_and_exact_run_association(self):
        run = "https://github.com/owner/repo/actions/runs/5"
        for case_id, body in sample_items():
            self.assertNotIn("[Current attempt]", body)
            self.assertNotIn("[Previous completed review]", body)
            with self.subTest(case_id=case_id):
                if case_id.startswith("shared-run-"):
                    current, previous = body.split("### Previous completed review")
                    self.assertIn("### Current attempt", current)
                    anchor = "Running" if case_id == "shared-run-in-progress" else "Attempt"
                    self.assertIn(f"[{anchor}]({run})", current)
                    self.assertIn(f"**[Reviewed]({run}) against:**", previous)
                    self.assertNotIn("**Attempt baseline:**", current)
                elif case_id == "legacy-history-run":
                    self.assertIn("**[Attempt](https://github.com/owner/repo/actions/runs/6) status:** Not recorded", body)
                    self.assertIn(f"**[Reviewed]({run}) target:**", body)
                elif case_id == "legacy-history-no-links":
                    self.assertNotIn("/actions/runs/", body)
                    self.assertIn("**Reviewed against:**", body)
                elif case_id.startswith("stale-running-"):
                    self.assertIn(f"**[Attempt]({run}) baseline:** " + gallery.check.commit_link(gallery.fixtures.BASE_TIP, gallery.fixtures.REPO), body)
                    self.assertEqual(gallery.check.status_state({"body": body}), "stale")
                    self.assertEqual(gallery.check.status_owner({"body": body})["head"], gallery.fixtures.HEAD)

    def test_stale_samples_use_bound_accepted_attempt_triggers(self):
        for case_id, body in sample_items():
            if not case_id.startswith("stale-"):
                continue
            record = {"body":body,"user":{"login":gallery.check.STATUS_AUTHOR,"type":"Bot"}}
            owner = gallery.check.status_owner(record)
            label = "Manual request" if case_id == "stale-manual" else "Draft marked ready"
            self.assertEqual(gallery.check.status_attempt_trigger(record, owner), label)
            self.assertIn("| " + label + " |", body.split("<details>")[0])
            if case_id == "stale-manual":
                self.assertIn("**Review trigger:** PR opened for review", body)

    def test_stale_explanations_identify_only_known_comparisons(self):
        cases = dict(sample_items())
        check = gallery.check
        commit = lambda sha: check.commit_link(sha, gallery.fixtures.REPO)
        expectations = {
            "stale-running-head": f"The current PR commit {commit(gallery.fixtures.OTHER)} is not covered by this attempt.",
            "stale-running-base": f"Its captured baseline is {commit(gallery.fixtures.BASE_TIP)} on `main`; the current target commit is {commit(gallery.fixtures.OTHER)}.",
            "stale-running-ref": "This attempt targets `main`; the PR now targets `release`.",
            "stale-manual": "This attempt's coverage of the current PR cannot be confirmed.",
            "legacy-history-run": "The applicability of this completed review to the current PR cannot be confirmed.",
            "workflow-changed-superseded": f"The review of {commit(gallery.fixtures.HEAD)} did not complete. The PR is now at {commit(gallery.fixtures.OTHER)}, which this attempt hasn’t reviewed.",
        }
        for case_id, expected in expectations.items():
            with self.subTest(case_id=case_id):
                self.assertIn(expected, cases[case_id].split("<details>")[0])
        for case_id in ("stale-running-head", "stale-running-base", "stale-running-ref", "stale-manual"):
            receipt = "**Reason:** " + check.STATUS_REASONS["stale"]
            self.assertIn("<!--\n" + receipt + "\n-->", cases[case_id])
        for case_id in ("workflow-changed", "workflow-changed-superseded"):
            body = cases[case_id]
            self.assertIn("likely caused this incomplete review", body.split("<details>")[0])
            self.assertIn("to confirm.", body.split("<details>")[0])
            self.assertEqual("`/claude-review`" in body, case_id == "workflow-changed")
            self.assertIn("**Reason:** ", body.split("<details>")[1])

    def test_generation_is_offline_without_writes_or_publication_and_restores_environment(self):
        original = os.environ.copy()
        with mock.patch.dict(os.environ, {
                "REPO": "private/secret", "DETAILS_URL": "https://private.invalid/run",
                "GITHUB_SERVER_URL": "https://private.invalid", "GITHUB_OUTPUT": "/must/not/write"}):
            before = os.environ.copy()
            with mock.patch("builtins.open", side_effect=AssertionError("file access during rendering")), \
                    mock.patch("os.open", side_effect=AssertionError("file access during rendering")), \
                    mock.patch.object(subprocess, "run", side_effect=AssertionError("subprocess")), \
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess")), \
                    mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
                    mock.patch.object(gallery.check, "gh_api", side_effect=AssertionError("publication")):
                body = gallery.render_gallery()
            self.assertEqual(os.environ, before)
            self.assertNotIn("private", body)
        self.assertEqual(os.environ, original)

    def test_environment_is_restored_on_renderer_error(self):
        before = os.environ.copy()
        with mock.patch.object(gallery.check, "status_comment_body", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                gallery.render_gallery()
        self.assertEqual(os.environ, before)

    def test_cli_stdout_write_and_exact_freshness_without_mutation(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(gallery.main([]), 0)
        expected = gallery.render_gallery().encode("utf-8")
        self.assertEqual(output.getvalue().encode("utf-8"), expected)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "gallery.md"
            with mock.patch.object(gallery, "GALLERY", target):
                for content in (None, b"stale", expected.replace(b"\n", b"\r\n")):
                    if content is not None:
                        target.write_bytes(content)
                    with mock.patch("sys.stderr", io.StringIO()) as error:
                        self.assertEqual(gallery.main(["--check"]), 1)
                    self.assertIn(gallery.REGENERATE, error.getvalue())
                    self.assertEqual(target.read_bytes() if target.exists() else None, content)
                self.assertEqual(gallery.main(["--write"]), 0)
                self.assertEqual(target.read_bytes(), expected)
                self.assertEqual(gallery.main(["--check"]), 0)

    def test_cli_is_stable_under_hostile_ambient_environment(self):
        expected = gallery.render_gallery().encode("utf-8")
        for seed in ("1", "123"):
            env = {"PYTHONHASHSEED": seed, "TZ": "Pacific/Honolulu",
                   "HOME": "/nonexistent-gallery-home", "GH_TOKEN": "synthetic-secret",
                   "REPO": "private/secret", "DETAILS_URL": "https://private.invalid",
                   "GITHUB_SERVER_URL": "https://private.invalid"}
            result = subprocess.run([sys.executable, "-B", str(SCRIPT)], env=env,
                                    capture_output=True, check=True)
            self.assertEqual(result.stdout, expected)
            self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
