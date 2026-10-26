"""Offline privacy and behavior checks for sanitized captured-read diagnostics."""
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import time
import unittest
from unittest import mock

import test_claude_review_workflows as workflows


PREFIX = "Claude review captured-input coverage: "
FIELDS = {"read_count", "successful_read_count", "expected_line_count", "matched_line_count",
          "missing_line_count", "unparsed_line_count", "content_shapes", "partial_view",
          "reads", "reads_truncated", "gap_range_count", "gap_ranges", "gap_ranges_truncated"}
COUNT_FIELDS = {"read_count", "successful_read_count", "expected_line_count", "matched_line_count",
                "missing_line_count", "unparsed_line_count", "gap_range_count"}
READ_FIELDS = {"request_offset", "request_limit", "response_count", "successful",
               "parsed_line_count", "overlap_line_count", "returned_range_count",
               "returned_ranges", "returned_ranges_truncated"}
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
            for key in COUNT_FIELDS:
                self.assertIs(type(stats[key]), int)
                self.assertGreaterEqual(stats[key], 0)
            self.assertIs(type(stats["partial_view"]), bool)
            self.assertIs(type(stats["reads_truncated"]), bool)
            self.assertIs(type(stats["gap_ranges_truncated"]), bool)
            self.assertIs(type(stats["content_shapes"]), list)
            self.assertTrue(set(stats["content_shapes"]) <= SHAPES)
            self.assertLessEqual(len(stats["reads"]), 16)
            self.assertLessEqual(len(stats["gap_ranges"]), 16)
            for item in stats["reads"]:
                self.assertEqual(set(item), READ_FIELDS)
                for key in ("request_offset", "request_limit"):
                    self.assertTrue(type(item[key]) is int or item[key] in (None, "invalid"))
                    if type(item[key]) is int:
                        self.assertGreaterEqual(item[key], 0)
                        self.assertLessEqual(item[key], 1000000000)
                for key in ("response_count", "parsed_line_count", "overlap_line_count", "returned_range_count"):
                    self.assertIs(type(item[key]), int)
                    self.assertGreaterEqual(item[key], 0)
                self.assertIs(type(item["successful"]), bool)
                self.assertIs(type(item["returned_ranges_truncated"]), bool)
                self.assertLessEqual(len(item["returned_ranges"]), 16)
                for returned in item["returned_ranges"]:
                    self.assertEqual(set(returned), {"start", "end"})
                    self.assertLessEqual(returned["start"], returned["end"])
            for gap in stats["gap_ranges"]:
                self.assertEqual(set(gap), {"start", "end"})
                self.assertLessEqual(gap["start"], gap["end"])
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
                    self.assertEqual(stats["reads"], [{
                        "request_offset": None, "request_limit": None, "response_count": 1,
                        "successful": True, "parsed_line_count": 2, "overlap_line_count": 0,
                        "returned_range_count": 1, "returned_ranges": [{"start": 1, "end": 2}],
                        "returned_ranges_truncated": False,
                    }])
                    self.assertEqual(stats["gap_ranges"], [])

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
                    self.assertEqual(stats["gap_ranges"], [{"start": 1, "end": 2}])

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
                        self.assertEqual(stats["reads"][1]["request_offset"], 2)
                        self.assertEqual(stats["reads"][1]["returned_ranges"], [{"start": 2, "end": 2}])
                        self.assertEqual(stats["reads"][1]["overlap_line_count"], 0)

    def test_historical_1793_line_shapes_expose_overlap_and_gaps_without_changing_verdict(self):
        diff = "\n".join(f"+sanitized line {number}" for number in range(1, 1794)) + "\n"
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            with self.subTest(path=path.name, shape="repeated-prefix"):
                stream = workflows.clean_stream()
                stream[4] = workflows.read_result("t2", diff, stop=1496)
                stream[5:5] = [
                    workflows.tool_use("repeat", "Read", file_path="/captured/diff.patch", offset=1497, limit=297),
                    workflows.read_result("repeat", diff, stop=1496),
                ]
                stats = self.verify(stream, path=path, expected=("false", "captured_inputs_not_read"), diff=diff)["diff"]
                self.assertEqual((stats["matched_line_count"], stats["missing_line_count"]), (1496, 297))
                self.assertEqual(stats["reads"][1]["request_offset"], 1497)
                self.assertEqual(stats["reads"][1]["request_limit"], 297)
                self.assertEqual(stats["reads"][1]["returned_ranges"], [{"start": 1, "end": 1496}])
                self.assertEqual(stats["reads"][1]["overlap_line_count"], 1496)
                self.assertEqual(stats["gap_ranges"], [{"start": 1497, "end": 1793}])

            with self.subTest(path=path.name, shape="correct-continuation"):
                stream[6] = workflows.read_result("repeat", diff, start=1497)
                stats = self.verify(stream, path=path, expected=("true", "verified"), diff=diff)["diff"]
                self.assertEqual(stats["reads"][1]["returned_ranges"], [{"start": 1497, "end": 1793}])
                self.assertEqual(stats["reads"][1]["overlap_line_count"], 0)
                self.assertEqual(stats["gap_ranges"], [])

            with self.subTest(path=path.name, shape="three-reads"):
                stream = workflows.clean_stream()
                stream[4] = workflows.read_result("t2", diff, stop=700)
                stream[5:5] = [
                    workflows.tool_use("middle", "Read", file_path="/captured/diff.patch", offset=701, limit=796),
                    workflows.read_result("middle", diff, start=701, stop=1496),
                    workflows.tool_use("tail", "Read", file_path="/captured/diff.patch", offset=1497, limit=297),
                    workflows.read_result("tail", diff, start=1497),
                ]
                stats = self.verify(stream, path=path, expected=("true", "verified"), diff=diff)["diff"]
                self.assertEqual(stats["read_count"], 3)
                self.assertEqual([item["returned_ranges"] for item in stats["reads"]], [
                    [{"start": 1, "end": 700}], [{"start": 701, "end": 1496}],
                    [{"start": 1497, "end": 1793}],
                ])
                self.assertEqual(stats["gap_ranges"], [])

    def test_diagnostic_lists_and_request_values_are_bounded_and_sanitized(self):
        diff = "\n".join(f"+line {number}" for number in range(1, 66)) + "\n"
        stream = workflows.clean_stream()
        rendered = "\n".join(f"{number:6d}→+line {number}" for number in range(1, 66, 2))
        stream[3]["message"]["content"][0]["input"].update(offset=SECRET, limit=10 ** 12)
        stream[4] = workflows.tool_result("t2", content=rendered)
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            with self.subTest(path=path.name, shape="bounded-ranges"):
                stats = self.verify(stream, path=path, expected=("false", "captured_inputs_not_read"), diff=diff)["diff"]
                self.assertEqual(stats["gap_range_count"], 32)
                self.assertEqual(len(stats["gap_ranges"]), 16)
                self.assertTrue(stats["gap_ranges_truncated"])
                self.assertEqual(stats["reads"][0]["returned_range_count"], 33)
                self.assertEqual(len(stats["reads"][0]["returned_ranges"]), 16)
                self.assertTrue(stats["reads"][0]["returned_ranges_truncated"])
        for number in range(16):
            use_id = f"extra-{number}"
            stream[5:5] = [workflows.tool_use(use_id, "Read", file_path="/captured/diff.patch"),
                           workflows.read_result(use_id, diff)]
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            with self.subTest(path=path.name):
                stats = self.verify(stream, path=path, expected=("true", "verified"), diff=diff)["diff"]
                self.assertEqual(stats["read_count"], 17)
                self.assertEqual(len(stats["reads"]), 16)
                self.assertTrue(stats["reads_truncated"])
                self.assertEqual(stats["reads"][0]["request_offset"], "invalid")
                self.assertEqual(stats["reads"][0]["request_limit"], "invalid")

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

    def test_partial_view_payload_is_not_a_banner(self):
        metadata = '{"description":"PARTIAL view is captured payload"}\n'
        diff = "+PARTIAL view is captured payload\n"
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            for blocks in (False, True):
                with self.subTest(path=path.name, blocks=blocks):
                    stream = workflows.clean_stream()
                    stream[2] = workflows.read_result("t1", metadata, blocks=blocks)
                    stream[4] = workflows.read_result("t2", diff, blocks=blocks)
                    report = self.verify(stream, path=path, expected=("true", "verified"), metadata=metadata, diff=diff)
                    self.assertFalse(report["metadata"]["partial_view"])
                    self.assertFalse(report["diff"]["partial_view"])
                    content = stream[4]["message"]["content"][0]
                    if blocks:
                        content["content"].insert(0, {"type":"text", "text":"PARTIAL view: read the remaining page"})
                    else:
                        content["content"] = "PARTIAL view: read the remaining page\n" + content["content"]
                    report = self.verify(stream, path=path, expected=("true", "verified"), metadata=metadata, diff=diff)
                    self.assertTrue(report["diff"]["partial_view"])
                    self.assertFalse(report["metadata"]["partial_view"])

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
                    for complete in (True, False):
                        harness = workflows.CompletionVerifierTests()
                        stream = workflows.clean_stream()
                        if not complete:
                            stream[4] = workflows.read_result("t2", workflows.DIFF_CONTENT, stop=1)
                        expected = ("true", "verified") if complete else ("false", "captured_inputs_not_read")
                        self.assertEqual(harness.verify(stream, path=path), expected)
                        self.assertNotIn(PREFIX, harness.last_stdout)
                        self.assertNotIn(SECRET, harness.last_stdout)

    def test_large_complete_and_overlapping_reads_have_exact_diagnostics(self):
        diff = "".join(f"+sanitized line {number}\n" for number in range(1, 10001))
        reports = {}
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            with self.subTest(path=path.name, shape="complete"):
                stream = workflows.clean_stream()
                stream[4] = workflows.read_result("t2", diff)
                stats = self.verify(stream, path=path, expected=("true", "verified"), diff=diff)["diff"]
                self.assertEqual(stats["matched_line_count"], 10000)
                self.assertEqual(stats["gap_ranges"], [])
                self.assertEqual(stats["reads"][0]["returned_ranges"], [{"start": 1, "end": 10000}])
                self.assertEqual(stats["reads"][0]["overlap_line_count"], 0)

            with self.subTest(path=path.name, shape="overlap-and-gap"):
                stream[4] = workflows.read_result("t2", diff, stop=8000)
                stream[5:5] = [
                    workflows.tool_use("overlap", "Read", file_path="/captured/diff.patch", offset=7001, limit=2000),
                    workflows.read_result("overlap", diff, start=7001, stop=9000),
                ]
                stats = self.verify(stream, path=path, expected=("false", "captured_inputs_not_read"), diff=diff)["diff"]
                self.assertEqual(stats["matched_line_count"], 9000)
                self.assertEqual(stats["missing_line_count"], 1000)
                self.assertEqual(stats["gap_ranges"], [{"start": 9001, "end": 10000}])
                self.assertEqual(stats["reads"][1]["overlap_line_count"], 1000)
                reports[path] = stats
        self.assertEqual(reports[workflows.MANUAL], reports[workflows.AUTOMATIC])

    def test_duplicate_and_changed_lines_preserve_range_and_exact_gap_semantics(self):
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            stream = workflows.clean_stream()
            stream[4] = workflows.tool_result("t2", content="1→incorrect\n1→" + workflows.DIFF_CONTENT.splitlines()[0])
            stream[5:5] = [
                workflows.tool_use("repeat", "Read", file_path="/captured/diff.patch"),
                workflows.tool_result("repeat", content="1→incorrect\n1→incorrect\n2→incorrect"),
            ]
            stats = self.verify(stream, path=path, expected=("false", "captured_inputs_not_read"))["diff"]
            self.assertEqual(stats["matched_line_count"], 1)
            self.assertEqual(stats["gap_ranges"], [{"start": 2, "end": 2}])
            self.assertEqual(stats["reads"][1]["parsed_line_count"], 3)
            self.assertEqual(stats["reads"][1]["returned_ranges"], [{"start": 1, "end": 2}])
            self.assertEqual(stats["reads"][1]["overlap_line_count"], 1)

    def test_diagnostic_membership_uses_keys_and_accumulates_prior_reads_once(self):
        # Guard the scale regression structurally, without machine-dependent
        # timing assertions. Array index membership or reparsing prior pages
        # would restore the quadratic work even when small fixtures pass.
        for path in (workflows.MANUAL, workflows.AUTOMATIC):
            script = workflows.run_block(workflows.steps(workflows.job(path, "review"))[workflows.VERIFY_STEP])
            coverage = script.split('if [ "${reason:-}" = captured_inputs_not_read ]', 1)[1]
            diagnostics = coverage.split("if diagnostics=", 1)[1]
            self.assertNotIn("| index(", diagnostics)
            self.assertNotIn("parsed_numbers", diagnostics)
            self.assertNotIn("range(0; $index)", diagnostics)
            self.assertIn("$matched[($number | tostring)]", diagnostics)
            self.assertIn("$prior[(tostring)]", diagnostics)
            self.assertIn("reduce $reads[0:16][]", diagnostics)

    def test_hung_diagnostics_are_killed_after_verdict_without_changing_outputs(self):
        executable = shutil.which("jq")
        self.assertIsNotNone(executable)
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "verdict-before-diagnostics"
            wrapper = Path(tmp) / "jq"
            wrapper.write_text(
                "#!/usr/bin/env python3\nimport os,sys,time\nfrom pathlib import Path\n"
                "if sys.argv[1] == '-c':\n"
                f" Path({str(marker)!r}).write_text(Path(os.environ['GITHUB_OUTPUT']).read_text())\n"
                " print('DIAGNOSTIC_OUTPUT_CANARY', flush=True)\n"
                " print('DIAGNOSTIC_OUTPUT_CANARY', file=sys.stderr, flush=True)\n"
                " time.sleep(60)\n"
                f"else:\n os.execv({executable!r}, [{executable!r}] + sys.argv[1:])\n"
            )
            wrapper.chmod(0o700)
            for path in (workflows.MANUAL, workflows.AUTOMATIC):
                for complete in (True, False):
                    with self.subTest(path=path.name, complete=complete):
                        stream = workflows.clean_stream()
                        if not complete:
                            stream[4] = workflows.read_result("t2", workflows.DIFF_CONTENT, stop=1)
                        harness = workflows.CompletionVerifierTests()
                        expected = harness.verify(stream, path=path)
                        outputs = harness.last_outputs
                        with mock.patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ["PATH"]}):
                            started = time.monotonic()
                            self.assertEqual(harness.verify(stream, path=path), expected)
                        # A generous liveness ceiling catches a missing deadline,
                        # not relative performance differences between machines.
                        self.assertLess(time.monotonic() - started, 20)
                        self.assertEqual(harness.last_outputs, outputs)
                        self.assertEqual(dict(line.split("=", 1) for line in marker.read_text().splitlines()), outputs)
                        self.assertNotIn(PREFIX, harness.last_stdout)
                        self.assertNotIn("DIAGNOSTIC_OUTPUT_CANARY", harness.last_stdout)
