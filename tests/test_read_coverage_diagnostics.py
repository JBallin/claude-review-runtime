"""Offline privacy and behavior checks for sanitized captured-read diagnostics."""
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import unittest
from unittest import mock

import test_claude_review_workflows as workflows


PREFIX = "Claude review captured-input coverage: "
FIELDS = {"read_count", "successful_read_count", "expected_line_count", "matched_line_count",
          "missing_line_count", "unparsed_line_count", "content_shapes", "partial_view"}
SHAPES = {"string", "text_blocks", "mixed_blocks", "other"}
SECRET = "PRIVATE_PAYLOAD_NEVER_PRINT_ME"


class ReadCoverageDiagnosticsTests(unittest.TestCase):
    def verify(self, stream, *, path, expected, **kwargs):
        harness = workflows.CompletionVerifierTests()
        self.assertEqual(harness.verify(stream, path=path, **kwargs), expected)
        lines = [line[len(PREFIX):] for line in harness.last_stdout.splitlines() if line.startswith(PREFIX)]
        self.assertEqual(len(lines), 1, harness.last_stdout)
        report = json.loads(lines[0])
        self.assertEqual(set(report), {"metadata", "diff"})
        for stats in report.values():
            self.assertEqual(set(stats), FIELDS)
            for key in FIELDS - {"content_shapes", "partial_view"}:
                self.assertIs(type(stats[key]), int)
                self.assertGreaterEqual(stats[key], 0)
            self.assertIs(type(stats["partial_view"]), bool)
            self.assertIs(type(stats["content_shapes"]), list)
            self.assertTrue(set(stats["content_shapes"]) <= SHAPES)
        self.assertNotIn(SECRET, harness.last_stdout)
        self.assertNotIn("/", harness.last_stdout)
        self.assertFalse(any("coverage" in key for key in harness.last_outputs))
        return report

    def test_complete_reads_report_counts_without_changing_verified_result(self):
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            with self.subTest(path=path.name):
                report = self.verify(workflows.clean_stream(), path=path, expected=("true", "verified"))
                for stats in report.values():
                    self.assertEqual(stats["read_count"], 1)
                    self.assertEqual(stats["successful_read_count"], 1)
                    self.assertEqual(stats["expected_line_count"], 2)
                    self.assertEqual(stats["matched_line_count"], 2)
                    self.assertEqual(stats["missing_line_count"], 0)
                    self.assertEqual(stats["unparsed_line_count"], 0)
                    self.assertEqual(stats["content_shapes"], ["string"])
                    self.assertFalse(stats["partial_view"])

    def test_missing_and_errored_reads_have_distinct_counts(self):
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            for kind in ("missing", "error", "wrong_path"):
                with self.subTest(path=path.name, kind=kind):
                    stream = workflows.clean_stream()
                    if kind == "missing":
                        del stream[3:5]
                    elif kind == "error":
                        stream[4]["message"]["content"][0]["is_error"] = True
                    else:
                        stream[3]["message"]["content"][0]["input"]["file_path"] = "/private/" + SECRET
                    stats = self.verify(stream, path=path, expected=("false", "captured_inputs_not_read"))["diff"]
                    self.assertEqual(stats["read_count"], 1 if kind == "error" else 0)
                    self.assertEqual(stats["successful_read_count"], 0)
                    self.assertEqual(stats["missing_line_count"], 2)

    def test_partial_pages_and_unknown_rendering_remain_rejected(self):
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            for kind in ("partial", "unknown"):
                with self.subTest(path=path.name, kind=kind):
                    stream = workflows.clean_stream()
                    response = workflows.read_result("t2", workflows.DIFF_CONTENT, stop=1)
                    block = response["message"]["content"][0]
                    block["content"] = "PARTIAL view " + SECRET + "\n" + block["content"] if kind == "partial" else "UNSUPPORTED " + SECRET
                    stream[4] = response
                    stats = self.verify(stream, path=path, expected=("false", "captured_inputs_not_read"))["diff"]
                    self.assertEqual(stats["successful_read_count"], 1)
                    self.assertEqual(stats["matched_line_count"], 1 if kind == "partial" else 0)
                    self.assertEqual(stats["unparsed_line_count"], 1)
                    self.assertEqual(stats["partial_view"], kind == "partial")
                    if kind == "partial":
                        stream[7:7] = [workflows.tool_use("tail", "Read", file_path="/captured/diff.patch", offset=2),
                                       workflows.read_result("tail", workflows.DIFF_CONTENT, start=2, blocks=True)]
                        stats = self.verify(stream, path=path, expected=("true", "verified"))["diff"]
                        self.assertEqual(stats["successful_read_count"], 2)
                        self.assertEqual(stats["missing_line_count"], 0)
                        self.assertEqual(stats["content_shapes"], ["string", "text_blocks"])

    def test_content_shape_enums_never_emit_payload_or_unknown_values(self):
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            for shape in ("text_blocks", "mixed_blocks", "other"):
                with self.subTest(path=path.name, shape=shape):
                    diff = "+" + SECRET + "\n+second\n"
                    stream = workflows.clean_stream()
                    response = workflows.read_result("t2", diff, blocks=True)
                    block = response["message"]["content"][0]
                    if shape == "mixed_blocks":
                        block["content"].append({"type": SECRET, "data": SECRET})
                    elif shape == "other":
                        block["content"] = {SECRET: SECRET}
                    stream[4] = response
                    expected = ("false", "captured_inputs_not_read") if shape == "other" else ("true", "verified")
                    stats = self.verify(stream, path=path, expected=expected, diff=diff)["diff"]
                    self.assertEqual(stats["content_shapes"], [shape])

    def test_invalid_execution_or_input_encoding_emits_no_diagnostics(self):
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            for kind in ("malformed", "tool_configuration", "encoding"):
                with self.subTest(path=path.name, kind=kind):
                    harness = workflows.CompletionVerifierTests()
                    stream = workflows.clean_stream()
                    kwargs = {}
                    if kind == "malformed":
                        kwargs["raw"] = SECRET
                        reason = "malformed_execution_file"
                    elif kind == "tool_configuration":
                        stream[0]["tools"].append("Agent")
                        reason = "review_tools_not_as_configured"
                    else:
                        kwargs["diff"] = b"+invalid \xff\n"
                        reason = "unsupported_captured_input_encoding"
                    self.assertEqual(harness.verify(stream, path=path, **kwargs), ("false", reason))
                    self.assertNotIn(PREFIX, harness.last_stdout)
                    self.assertNotIn(SECRET, harness.last_stdout)

    def test_diagnostic_failure_cannot_change_verdict_or_expose_stderr(self):
        executable = shutil.which("jq")
        self.assertIsNotNone(executable)
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "jq"
            wrapper.write_text('#!/bin/sh\nif [ "$1" = -c ]; then\n echo ' + SECRET + ' >&2\n exit 1\nfi\nexec ' + shlex.quote(executable) + ' "$@"\n')
            wrapper.chmod(0o700)
            with mock.patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                for path in (workflows.MANUAL, workflows.AUTOMATIC):
                    harness = workflows.CompletionVerifierTests()
                    self.assertEqual(harness.verify(workflows.clean_stream(), path=path), ("true", "verified"))
                    self.assertNotIn(PREFIX, harness.last_stdout)
                    self.assertNotIn(SECRET, harness.last_stdout)
