"""Offline regression tests for the reusable workflow and bundled-action seam."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / "actions" / "review-helper" / "action.yml"
HELPER = ACTION.parent / "claude_review_check.py"
WORKFLOWS = ROOT / ".github" / "workflows"
ENTRYPOINTS = ("claude-review.yml", "claude.yml", "claude-review-status.yml")


def preparation_script():
    lines = ACTION.read_text().splitlines()
    start = lines.index("      run: |")
    body = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= 6:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


class BundledHelperTests(unittest.TestCase):
    def run_preparation(self, command, helper_source=HELPER):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            action_path = root / "runtime bundle"
            action_path.mkdir()
            consumer = root / "consumer"
            (consumer / "scripts").mkdir(parents=True)
            # Both a conventional consumer helper and one beside the working
            # directory must be ignored, even if they appear executable.
            sentinel = root / "consumer-helper-ran"
            malicious = f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
            (consumer / "scripts" / "claude_review_check.py").write_text(malicious)
            (consumer / "claude_review_check.py").write_text(malicious)
            if helper_source is not None:
                shutil.copyfile(helper_source, action_path / "claude_review_check.py")
            binary = root / "bin"
            binary.mkdir()
            gh_log = root / "gh-called"
            gh = binary / "gh"
            gh.write_text(f"#!/bin/sh\ntouch '{gh_log}'\nexit 91\n")
            gh.chmod(0o755)
            output = root / "output"
            environment = {
                "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
                "ACTION_PATH": str(action_path),
                "HELPER_COMMAND": command,
                "GITHUB_OUTPUT": str(output),
            }
            result = subprocess.run(["bash", "-eo", "pipefail", "-c", preparation_script()],
                                    cwd=consumer, env=environment, text=True, capture_output=True)
            written = output.read_text() if output.exists() else ""
            self.assertFalse(sentinel.exists(), "Preparation executed a consumer helper")
            self.assertFalse(gh_log.exists(), "Preparation attempted API access")
            return result, written, str(action_path / "claude_review_check.py")

    def test_preparation_selects_the_bundle_for_every_supported_command(self):
        for command in ("create", "finalize", "stale"):
            with self.subTest(command=command):
                result, written, helper = self.run_preparation(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(written, f"helper-path={helper}\n")
                self.assertTrue(Path(helper).is_absolute())

    def test_missing_bundle_fails_without_falling_back_to_consumer_helper(self):
        result, written, _ = self.run_preparation("finalize", helper_source=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(written, "")

    def test_bundle_without_supported_command_is_not_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            legacy = Path(temporary) / "legacy.py"
            legacy.write_text("import argparse\np=argparse.ArgumentParser()\n"
                              "p.add_argument('command', choices=['other'])\np.parse_args()\n")
            result, written, _ = self.run_preparation("finalize", helper_source=legacy)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(written, "")

    def test_arbitrary_commands_are_rejected(self):
        for command in ("snapshot", "--help", "finalize; touch injected", ""):
            with self.subTest(command=command):
                result, written, _ = self.run_preparation(command)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(written, "")

    def test_action_path_and_command_are_passed_as_environment_data(self):
        text = ACTION.read_text()
        self.assertIn("ACTION_PATH: ${{ github.action_path }}", text)
        self.assertIn("HELPER_COMMAND: ${{ inputs.command }}", text)
        self.assertIn("value: ${{ steps.prepare.outputs.helper-path }}", text)
        self.assertNotIn("${{", preparation_script())


class CapturedInputTests(unittest.TestCase):
    def test_capture_preserves_hostile_text_and_exact_runtime_head_base(self):
        from test_claude_review_workflows import job, steps, run_block

        head, base, merge_base, runtime_sha = "a" * 40, "b" * 40, "c" * 40, "f" * 40
        for workflow, step, filename in (("claude-review.yml", "Fetch PR review inputs", "pr.json"),
                                         ("claude.yml", "Capture PR review snapshot", "request.json")):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                review = root / "pr-review"
                review.mkdir()
                sentinel = root / "injected"
                hostile = f"Untrusted $(touch {sentinel}) `touch {sentinel}` \\n\n'quoted' \"text\""
                raw = {"number": 7, "title": hostile, "body": hostile}
                (review / "pr-raw.json").write_text(json.dumps(raw))
                stub = root / "gh"
                stub.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                                "from pathlib import Path\n"
                                "with open(os.environ['GH_LOG'], 'a') as log:\n"
                                " log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                                "print(os.environ['MERGE_BASE'] if '--jq' in sys.argv else 'diff content')\n")
                stub.chmod(0o755)
                log = root / "gh.log"
                output = root / "output"
                environment = {
                    "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                    "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(output),
                    "GH_LOG": str(log), "MERGE_BASE": merge_base,
                    "REPO": "example/consumer", "PR_NUMBER": "7", "PR_TITLE": hostile,
                    "PR_BODY": hostile, "TRIGGER_COMMENT": hostile, "BASE_REF": "main",
                    "HEAD_SHA": head, "BASE_SHA": base,
                    "RUNTIME_REPOSITORY": "example/review-runtime", "RUNTIME_SHA": runtime_sha,
                    "RUNTIME_WORKFLOW_PATH": f".github/workflows/{workflow}",
                }
                script = run_block(steps(job(WORKFLOWS / workflow, "start-check"))[step])
                result = subprocess.run(["bash", "-eo", "pipefail", "-c", script],
                                        cwd=root, env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                captured = json.loads((review / filename).read_text())
                self.assertEqual((captured["number"], captured["title"], captured["body"]), (7, hostile, hostile))
                self.assertEqual((captured["head_sha"], captured["base_sha"]), (head, base))
                self.assertEqual((captured["runtime_repository"], captured["runtime_sha"], captured["runtime_workflow_path"]),
                                 ("example/review-runtime", runtime_sha, f".github/workflows/{workflow}"))
                if workflow == "claude.yml":
                    self.assertEqual(captured["trigger_comment"], hostile)
                    self.assertEqual(captured["base_ref"], "main")
                    self.assertFalse((review / "pr-raw.json").exists())
                self.assertFalse(sentinel.exists())
                self.assertEqual(output.read_text(), f"merge_base_sha={merge_base}\n")
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0][1], f"repos/example/consumer/compare/{base}...{head}")
                self.assertEqual(calls[1][1], f"repos/example/consumer/compare/{base}...{head}?per_page=1")


class ReusableBoundaryTests(unittest.TestCase):
    def test_entrypoints_only_expose_workflow_call_and_the_named_secret(self):
        for name in ENTRYPOINTS:
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text()
                trigger = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
                self.assertIn("  workflow_call:", trigger)
                self.assertNotIn("inputs:", trigger)
                for event in ("pull_request:", "issue_comment:", "workflow_dispatch:", "push:"):
                    self.assertNotIn(event, trigger)
                if name == "claude-review-status.yml":
                    self.assertNotIn("secrets:", trigger)
                else:
                    self.assertEqual(re.findall(r"^      ([A-Z_]+):$", trigger, re.MULTILINE),
                                     ["ANTHROPIC_API_KEY"])
                    self.assertIn("required: true", trigger)
                self.assertNotIn("secrets: inherit", text)

    def test_runtime_identity_comes_from_the_called_workflow_context(self):
        for name in ENTRYPOINTS:
            with self.subTest(workflow=name):
                text = (WORKFLOWS / name).read_text()
                for variable, context in (("RUNTIME_REPOSITORY", "workflow_repository"),
                                          ("RUNTIME_SHA", "workflow_sha"),
                                          ("RUNTIME_WORKFLOW_PATH", "workflow_file_path")):
                    values = re.findall(rf"^          {variable}: (.+)$", text, re.MULTILINE)
                    self.assertTrue(values)
                    self.assertEqual(set(values), {"${{ job." + context + " }}"})
                if name != "claude-review-status.yml":
                    for field in ("runtime_repository", "runtime_sha", "runtime_workflow_path"):
                        self.assertGreaterEqual(text.count(f"{field}: ${field}"), 2,
                                                "Captured input and failure evidence need runtime identity")

    def test_remote_dependencies_are_pinned_and_helper_uses_same_revision(self):
        for name in ENTRYPOINTS:
            with self.subTest(workflow=name):
                uses = re.findall(r"^        uses: (.+)$", (WORKFLOWS / name).read_text(), re.MULTILINE)
                self.assertIn("$/actions/review-helper", uses)
                for reference in uses:
                    if reference.startswith("$/"):
                        self.assertEqual(reference, "$/actions/review-helper")
                    else:
                        self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
