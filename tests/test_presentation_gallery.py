"""Offline gallery safety and renderer-reuse contracts."""

from contextlib import redirect_stdout
import importlib.util
import io
import os
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


class PresentationGalleryTests(unittest.TestCase):
    def test_deterministic_complete_gallery(self):
        body = gallery.render_gallery()
        self.assertEqual(body, gallery.render_gallery())
        self.assertTrue(body.startswith("# " + gallery.HEADING + "\n"))
        self.assertNotIn("Source revision", body)
        self.assertNotIn("Pending —", body)
        self.assertNotIn("Visual-QA observations", body)
        self.assertEqual(len(gallery.gallery_samples()), 34)
        for letter in "ABCDEFGHIJKLMNOPQR":
            self.assertIn(f"### {letter} — ", body)
        # Keep conservative byte headroom below GitHub's 65,536-character limit.
        self.assertLess(len(body.encode("utf-8")), 60_000)

    def test_each_sample_reuses_helper_output_with_only_markers_and_hard_breaks_normalized(self):
        check = gallery.check
        with mock.patch.object(check, "status_comment_body", wraps=check.status_comment_body) as status:
            with mock.patch.object(check, "completion_comment_body", wraps=check.completion_comment_body) as completion:
                samples = gallery.gallery_samples()
        self.assertEqual(status.call_count, 32)
        self.assertEqual(completion.call_count, 2)
        self.assertEqual([call.kwargs["observed_base_sha"] for call in completion.call_args_list],
                         [gallery.fixtures.BASE_TIP, gallery.fixtures.OTHER])
        body = gallery.render_gallery()
        for title, raw in samples:
            with self.subTest(title=title):
                self.assertIn(gallery.sample_markdown(raw), body)
                self.assertTrue("claude-review-runtime:" in raw)

    def test_current_findings_completion_has_current_commit_run_and_evidence(self):
        check = gallery.check
        with mock.patch.object(check, "status_comment_body", wraps=check.status_comment_body) as status:
            samples = gallery.gallery_samples()
        title, raw = samples[1]
        self.assertEqual(title, gallery.FINDINGS_SAMPLE)
        call = status.call_args_list[1]
        self.assertEqual(call.args, (gallery.fixtures.HEAD, "main", "action_required"))
        self.assertNotIn("last_review", call.kwargs)
        visible, details = raw.split("<details>", 1)
        self.assertIn("⚠️ **Findings**", visible)
        self.assertIn("| Reviewed commit |", visible)
        self.assertIn(check.commit_link(gallery.fixtures.HEAD, gallery.fixtures.REPO), visible)
        self.assertIn("PR opened for review", visible)
        self.assertIn(gallery.COMPLETED_AT, visible)
        self.assertIn("Assess the recorded findings in the inline review threads.", visible)
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

    def test_gallery_cannot_be_treated_as_an_owned_record_even_for_bot_author(self):
        body = gallery.render_gallery()
        self.assertNotIn("claude-review-runtime:", body)
        self.assertNotIn("@claude", body)
        self.assertNotIn("@codex", body)
        comment = {"body": body, "user": {"login": gallery.check.STATUS_AUTHOR, "type": "Bot"}}
        self.assertFalse(gallery.check.owned_status(comment))
        self.assertIsNone(gallery.check.status_owner(comment))
        self.assertIsNone(gallery.check.status_head(comment))
        self.assertNotIn(gallery.check.COMPLETION_PREFIX, body)

    def test_strip_only_operational_comments_preserves_visible_copy_and_reason(self):
        reason = "<!--\n**Reason:** " + gallery.check.STATUS_REASONS["base_advanced"] + "\n-->"
        expected = "## Heading\n\n" + reason + "\nvisible\n<!-- unrelated -->"
        raw = gallery.check.STATUS_MARKER + "\n" + expected
        self.assertEqual(gallery.strip_operational_markers(raw), expected)

    def test_hard_break_normalization_preserves_lines_without_trailing_whitespace(self):
        raw = gallery.check.STATUS_MARKER + "\nfirst  \nsecond\n"
        self.assertEqual(gallery.sample_markdown(raw), "first\\\nsecond\n")
        self.assertTrue(all(line == line.rstrip() for line in gallery.render_gallery().splitlines()))

    def test_real_samples_keep_legacy_reason_hidden_and_label_identity_roles(self):
        samples = dict(gallery.gallery_samples())
        for title, raw in samples.items():
            if title.startswith(("B —", "C —")):
                with self.subTest(title=title):
                    body = gallery.strip_operational_markers(raw)
                    receipt = "<!--\n**Reason:** " + gallery.check.STATUS_REASONS["base_advanced"] + "\n-->"
                    self.assertIn(receipt, body)
                    self.assertIn("**Current target commit:**", body)
                    self.assertIn("| Reviewed commit |", body)
                    self.assertEqual(body.count(
                        "⚠️ The target branch differs from the reviewed baseline. Integration with its current version hasn’t been reviewed."
                    ), 1)
            elif title.startswith("E —"):
                self.assertIn("| Attempt commit |", raw)
            elif title.startswith("F —"):
                self.assertIn("Claude Review: No findings. 🎉", raw)
                self.assertIn("**Reviewed commit:**", raw)
                self.assertNotIn(gallery.check.TARGET_TIP_WARNING, raw)
                self.assertNotIn("target commit:**", raw)
                self.assertNotIn(gallery.check.CAPTURED_PATCH_SCOPE, raw)
                self.assertNotIn("This is not merge approval.", raw)
            elif title.startswith("G —"):
                self.assertIn("Claude Review: No findings. 🎉", raw)
                self.assertIn(gallery.check.TARGET_TIP_WARNING, raw.split("<details>")[0])
                self.assertNotIn(gallery.check.CAPTURED_PATCH_SCOPE, raw)
                self.assertNotIn("This is not merge approval.", raw)
                self.assertIn("**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** " + gallery.check.commit_link(gallery.fixtures.BASE_TIP, gallery.fixtures.REPO), raw)
                self.assertIn("**Observed target commit:** " + gallery.check.commit_link(gallery.fixtures.OTHER, gallery.fixtures.REPO), raw)
                self.assertNotIn("**Current target commit:**", raw)

    def test_publication_failures_and_unavailable_check_variants_use_current_attempt(self):
        samples = dict(gallery.gallery_samples())
        groups = {letter: [(t, b) for t, b in samples.items() if t.startswith(letter + " —")]
                  for letter in ("K", "L", "M")}
        for letter, cases in groups.items():
            self.assertEqual(len(cases), 4 if letter == "K" else 3)
            for title, body in cases:
                with self.subTest(title=title):
                    visible, details = body.split("<details>", 1)
                    self.assertIn("| Attempt commit |", visible)
                    self.assertIn("| " + gallery.check.commit_link(gallery.fixtures.OTHER, gallery.fixtures.REPO)
                                  + (" | Manual request |" if "Manual current attempt" in title else " | Draft marked ready |"), visible)
                    self.assertNotIn("✅ **Completed**", visible)
                    self.assertNotIn("⚠️ **Findings**", visible)
                    self.assertIn("🔄 **Running**" if letter == "M" else "❌ **Incomplete**", visible)
                    if letter == "K":
                        self.assertIn("The authoritative Check result could not be published.", visible)
                    else:
                        self.assertIn("authoritative Check publication is unavailable", visible)
                    if "no completed history" in title:
                        self.assertNotIn("**Previous completed review**", details)
                    else:
                        self.assertIn("**Current attempt**", details)
                        self.assertIn("\n\n**Previous completed review**  \n", details)
                        self.assertIn("**Result:** " + ("Findings." if "previous findings" in title else "No findings."), details)
                        self.assertEqual(gallery.check.last_completed_review({"body": body}),
                                         (gallery.fixtures.HEAD, "main", "action_required" if "previous findings" in title else "success"))

    def test_manual_history_samples_attach_scope_only_to_previous_review(self):
        cases = [(title, body) for title, body in gallery.gallery_samples()
                 if "Manual current attempt" in title]
        self.assertEqual(len(cases), 3)
        for title, body in cases:
            with self.subTest(title=title):
                current, previous = body.split("**Previous completed review**")
                self.assertNotIn(gallery.check.CAPTURED_PATCH_SCOPE, current)
                self.assertEqual(previous.count(gallery.check.CAPTURED_PATCH_SCOPE), 1)
                self.assertIn("**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:**", previous)
                self.assertIn("**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:**", current)

    def test_compact_metadata_keeps_links_on_consecutive_rendered_lines(self):
        for title, body in gallery.gallery_samples():
            if title.startswith(("A —", "B —", "C —", "F —", "G —", "J —")):
                with self.subTest(title=title):
                    details = body.split("<details>")[1]
                    self.assertNotIn("\n\n[", details)
                    self.assertIn("**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:**", details)
                    if title.startswith(("A —", "B —")):
                        self.assertIn("**Result:** No findings.  \n**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:**", details)
                    if title.startswith(("F —", "G —")):
                        self.assertNotIn("captured patch only", body)
                        self.assertNotIn("merge approval", body)

    def test_run_links_are_embedded_in_the_correct_sample_context(self):
        reviewed = "https://github.com/owner/repo/actions/runs/5"
        attempt = "https://github.com/owner/repo/actions/runs/6"
        for title, body in gallery.gallery_samples():
            with self.subTest(title=title):
                details = body.split("<details>")[1]
                self.assertNotIn("workflow run]", details)
                self.assertNotIn("[Current attempt]", details)
                self.assertNotIn("[Previous completed review]", details)
                self.assertNotIn("[Review run]", details)
                if title.startswith(("N —", "O —", "P —", "Q —", "R —")):
                    continue
                if "**Previous completed review**" in details:
                    current, previous = details.split("**Previous completed review**")
                    self.assertIn("**Current attempt**", current)
                    self.assertIn(f"**[Attempt]({attempt}) baseline:**", current)
                    self.assertIn(f"**[Reviewed]({reviewed}) against:**", previous)
                    self.assertNotIn(reviewed, current)
                    self.assertNotIn(attempt, previous)
                elif title.startswith(("E —", "K —", "L —")):
                    url = reviewed if title.startswith("E —") else attempt
                    self.assertIn(f"**[Attempt]({url}) baseline:**", details)
                    self.assertNotIn("[Reviewed]", details)
                elif title.startswith("M —"):
                    self.assertIn(f"**[Attempt]({attempt}) baseline:**", details)
                    self.assertNotIn("[Reviewed]", details)
                else:
                    self.assertIn(f"**[Reviewed]({reviewed}) against:**", details)

    def test_edge_samples_keep_plain_headings_and_exact_run_association(self):
        run = "https://github.com/owner/repo/actions/runs/5"
        for title, body in gallery.gallery_samples():
            self.assertNotIn("[Current attempt]", body)
            self.assertNotIn("[Previous completed review]", body)
            with self.subTest(title=title):
                if title.startswith(("N —", "O —")):
                    current, previous = body.split("**Previous completed review**")
                    self.assertEqual("**Current attempt**" in current, title.startswith("O —"))
                    anchor = "Running" if title.startswith("N —") else "Attempt"
                    self.assertIn(f"[{anchor}]({run})", current)
                    self.assertIn(f"**[Reviewed]({run}) against:**", previous)
                    self.assertNotIn("**Attempt baseline:**", current)
                elif title.startswith("P —"):
                    self.assertIn("**[Attempt](https://github.com/owner/repo/actions/runs/6) status:** Not recorded", body)
                    self.assertIn(f"**[Reviewed]({run}) target:**", body)
                elif title.startswith("Q —"):
                    self.assertNotIn("/actions/runs/", body)
                    self.assertIn("**Reviewed against:**", body)
                elif title.startswith("R — Stale running"):
                    self.assertIn(f"**[Attempt]({run}) baseline:** " + gallery.check.commit_link(gallery.fixtures.BASE_TIP, gallery.fixtures.REPO), body)
                    self.assertEqual(gallery.check.status_state({"body": body}), "stale")
                    self.assertEqual(gallery.check.status_owner({"body": body})["head"], gallery.fixtures.HEAD)

    def test_stale_samples_use_bound_accepted_attempt_triggers(self):
        for title, body in gallery.gallery_samples():
            if not title.startswith("R —"):
                continue
            record = {"body":body,"user":{"login":gallery.check.STATUS_AUTHOR,"type":"Bot"}}
            owner = gallery.check.status_owner(record)
            label = "Manual request" if "manual" in title else "Draft marked ready"
            self.assertEqual(gallery.check.status_attempt_trigger(record, owner), label)
            self.assertIn("| " + label + " |", body.split("<details>")[0])
            if "manual" in title:
                self.assertIn("**Review trigger:** PR opened for review", body)

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
