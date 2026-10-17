"""Offline gallery safety and renderer-reuse contracts."""

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_presentation_gallery.py"
spec = importlib.util.spec_from_file_location("presentation_gallery", SCRIPT)
gallery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gallery)
REVISION = "1" * 40


class PresentationGalleryTests(unittest.TestCase):
    def test_deterministic_complete_gallery_fits_one_comment(self):
        body = gallery.render_gallery(REVISION)
        self.assertEqual(body, gallery.render_gallery(REVISION))
        self.assertTrue(body.startswith("# " + gallery.HEADING + "\n"))
        self.assertIn(f"{gallery.SOURCE_REPOSITORY}/commit/{REVISION}", body)
        self.assertIn(gallery.PENDING_G, body)
        self.assertIn("**Not yet inspected.**", body)
        self.assertEqual(len(gallery.gallery_samples()), 13)
        for letter in "ABCDEFGHIJ":
            self.assertIn(f"### {letter} — ", body)
        # Keep conservative byte headroom below GitHub's 65,536-character limit.
        self.assertLess(len(body.encode("utf-8")), 60_000)

    def test_each_sample_reuses_helper_output_with_only_markers_removed(self):
        check = gallery.check
        with mock.patch.object(check, "status_comment_body", wraps=check.status_comment_body) as status:
            with mock.patch.object(check, "completion_comment_body", wraps=check.completion_comment_body) as completion:
                samples = gallery.gallery_samples()
        self.assertEqual(status.call_count, 11)
        self.assertEqual(completion.call_count, 1)
        body = gallery.render_gallery(REVISION)
        for title, raw in samples:
            with self.subTest(title=title):
                if raw is None:
                    self.assertIn(f"### {title}\n\n{gallery.PENDING_G}", body)
                else:
                    self.assertIn(gallery.strip_operational_markers(raw), body)
                    self.assertTrue("claude-review-runtime:" in raw)

    def test_gallery_cannot_be_treated_as_an_owned_record_even_for_bot_author(self):
        body = gallery.render_gallery(REVISION)
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

    def test_real_samples_keep_legacy_reason_hidden_and_label_identity_roles(self):
        samples = dict(gallery.gallery_samples())
        for title, raw in samples.items():
            if title.startswith(("B —", "C —")):
                with self.subTest(title=title):
                    body = gallery.strip_operational_markers(raw)
                    receipt = "<!--\n**Reason:** " + gallery.check.STATUS_REASONS["base_advanced"] + "\n-->"
                    self.assertIn(receipt, body)
                    self.assertIn("**Observed target tip:**", body)
                    self.assertIn("| Reviewed commit |", body)
                    self.assertEqual(body.count(
                        "⚠️ The target tip differs from the reviewed baseline; integration with that tip has not been reviewed."
                    ), 1)
            elif title.startswith("E —"):
                self.assertIn("| Attempt commit |", raw)
            elif title.startswith("F —"):
                self.assertIn("Claude Review: No findings. 🎉", raw)
                self.assertIn("**Reviewed commit:**", raw)
                self.assertNotIn("integration with that tip", raw)

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
                body = gallery.render_gallery(REVISION)
            self.assertEqual(os.environ, before)
            self.assertNotIn("private", body)
        self.assertEqual(os.environ, original)

    def test_environment_is_restored_on_renderer_error(self):
        before = os.environ.copy()
        with mock.patch.object(gallery.check, "status_comment_body", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                gallery.render_gallery(REVISION)
        self.assertEqual(os.environ, before)

    def test_cli_writes_gallery_to_stdout_and_requires_full_sha(self):
        with redirect_stdout(io.StringIO()) as output:
            gallery.main(["--source-revision", REVISION])
        self.assertEqual(output.getvalue(), gallery.render_gallery(REVISION))
        for invalid in ("", "main", "a" * 7, "A" * 40, "g" * 40, "a" * 41):
            with self.subTest(invalid=invalid):
                with self.assertRaises(argparse.ArgumentTypeError):
                    gallery.render_gallery(invalid)
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as missing:
                gallery.main([])
        self.assertEqual(missing.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
