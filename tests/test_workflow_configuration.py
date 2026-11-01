"""Helper-independent configuration admission and fail-closed job gates, offline."""
import ast
import inspect
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_claude_review_workflows as w

check = w.check


def preflight(path, model="", effort="", *, fail_after_write=False, broken_summary=False):
    step = w.steps(w.job(path, "start-check"))["Validate workflow configuration"]
    script = w.run_block(step)
    if fail_after_write:
        script = script.replace("\nPY_CONFIGURATION", "\nraise RuntimeError('synthetic failure after output')\nPY_CONFIGURATION")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        output, summary, calls = root / "outputs", root / "summary", root / "calls"
        stub = root / "gh"
        stub.write_text("#!/bin/sh\nprintf 'unexpected API call\\n' >> \"$GH_STUB_LOG\"\nexit 1\n")
        stub.chmod(0o755)
        result = subprocess.run(["bash", "-eo", "pipefail", "-c", script], capture_output=True, text=True,
                                env={"PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                                     "GITHUB_OUTPUT": str(output), "GH_STUB_LOG": str(calls),
                                     "GITHUB_STEP_SUMMARY": str(root if broken_summary else summary),
                                     "WORKFLOW_REVIEW_MODEL": model, "WORKFLOW_REVIEW_EFFORT": effort})
        values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
        return result, values, summary.read_text() if summary.exists() else "", calls.exists()


class WorkflowConfigurationTests(unittest.TestCase):
    def test_preflight_functions_match_helper_and_run_before_any_review_state(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            named = w.steps(w.job(path, "start-check"))
            self.assertEqual(next(iter(named)), "Validate workflow configuration")
            step = named["Validate workflow configuration"]
            source = w.run_block(step).split("\n", 1)[1].rsplit("PY_CONFIGURATION", 1)[0]
            tree = ast.parse(source)
            for name in ("invalid_workflow_fields", "configuration_error_body"):
                inline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
                helper = ast.parse(inspect.getsource(getattr(check, name))).body[0]
                self.assertEqual(ast.dump(inline), ast.dump(helper))
            for forbidden in ("GH_TOKEN", "REVIEW_HELPER", "PRESENTATION_START", "uses:", "subprocess", "actions/checkout"):
                self.assertNotIn(forbidden, "\n".join(step))

    def test_all_supported_and_omitted_values_pass_both_actual_preflights(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            for model in ("", *check.MODELS):
                for effort in ("", *check.EFFORTS):
                    with self.subTest(path=path.name, model=model, effort=effort):
                        result, values, summary, calls = preflight(path, model, effort)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(values, {"configuration_valid": "true"})
                        self.assertEqual(summary, "")
                        self.assertFalse(calls)
            for model, effort in ((" \t", "\n"), (" SoNnEt\n", "\tHIGH ")):
                self.assertEqual(preflight(path, model, effort)[0].returncode, 0)

    def test_invalid_values_have_safe_field_guidance_no_execution_values_or_api_calls(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            for model, effort, fields in (("opus[1m]", "high", ["model"]),
                                          ("haiku", "ultra", ["effort"]),
                                          ("invalid", "invalid", ["model", "effort"]),
                                          ("opus\n--tools Bash; $(id) <script>", "high", ["model"])):
                with self.subTest(path=path.name, model=model, effort=effort):
                    result, values, summary, calls = preflight(path, model, effort)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(values, {})
                    self.assertEqual(summary, check.configuration_error_body(fields) + "\n")
                    self.assertFalse(calls)
                    for field in fields:
                        self.assertIn("::error::Invalid workflow " + field + " input", result.stdout)
                    self.assertNotIn("$(id)", result.stdout + result.stderr + summary)

    def test_configuration_guidance_rejects_unknown_or_ambiguous_field_lists(self):
        for fields in ([], ["unknown"], ["model", "model"], "model", None):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                check.configuration_error_body(fields)
        self.assertEqual(check.configuration_error_body(["effort", "model"]),
                         check.configuration_error_body(["model", "effort"]))

    def test_failed_or_missing_preflight_never_authorizes_check_model_or_publisher(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            start = w.job(path, "start-check")
            output = w.scalar(w.block(start, "outputs", 4), "configuration_valid", 6)
            check_gate = w.scalar(w.steps(start)["Start Claude Review check"], "if", 8)
            review_gate = w.scalar(w.job(path, "review"), "if", 4)
            publish_gate = w.scalar(w.job(path, "publish-status"), "if", 4)
            for outcome in ("success", "failure", "skipped", ""):
                for flag in ("true", "false", ""):
                    state = {"configuration": {"outcome": outcome, "outputs": {"configuration_valid": flag}},
                             "select": {"outcome": "success"}, "tooling-ready": {"outcome": "success"}}
                    expected = outcome == "success" and flag == "true"
                    valid = w.Evaluator(w.unwrap(output), {"steps": state}).evaluate()
                    self.assertEqual(bool(valid), expected)
                    self.assertEqual(bool(w.Evaluator(check_gate, {"steps": state}).evaluate()), expected)
                    context = {"needs": {"request": {"result": "success", "outputs": {"classification": "valid"}},
                                         "start-check": {"result": "failure", "outputs": {
                                             "configuration_valid": w.to_string(valid), "request_recognized": "true",
                                             "selection_accepted": "false", "tooling_unavailable": "true"}}}}
                    self.assertEqual(bool(w.Evaluator(review_gate, context).evaluate()), expected)
                    self.assertEqual(bool(w.Evaluator(publish_gate, context).evaluate()), expected)

    def test_post_output_failure_and_summary_failure_do_not_create_an_admitted_step(self):
        for path in (w.AUTOMATIC, w.MANUAL):
            result, values, _, calls = preflight(path, fail_after_write=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(values, {"configuration_valid": "true"})
            output = w.scalar(w.block(w.job(path, "start-check"), "outputs", 4), "configuration_valid", 6)
            self.assertFalse(w.Evaluator(w.unwrap(output), {"steps": {"configuration": {
                "outcome": "failure", "outputs": values}}}).evaluate())
            self.assertFalse(calls)
            result, values, _, calls = preflight(path, "invalid", broken_summary=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(values, {})
            self.assertFalse(calls)


if __name__ == "__main__":
    unittest.main()
