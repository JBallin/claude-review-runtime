"""Offline contracts for this repository's pinned self-review callers."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_claude_review_workflows as w

PIN = "250a7d6745285dcc7902b604aec9f62f5b204033"
AUTOMATIC = w.WORKFLOWS / "self-review-automatic.yml"
MANUAL = w.WORKFLOWS / "self-review-manual.yml"
STATUS = w.WORKFLOWS / "self-review-status.yml"
CALLERS = ((AUTOMATIC, "review", "claude-review.yml"),
           (MANUAL, "review", "claude.yml"),
           (STATUS, "status", "claude-review-status.yml"))


def permissions(lines, indent):
    return dict(re.findall(rf"^{' ' * indent}([\w-]+): (read|write)$",
                           "\n".join(lines), re.MULTILINE))


class CallerBoundaryTests(unittest.TestCase):
    def test_publication_uses_one_immutable_runtime_not_candidate_code(self):
        for path, name, entrypoint in CALLERS:
            with self.subTest(caller=path.name):
                text = path.read_text()
                called = w.job(path, name)
                self.assertEqual(w.scalar(called, "uses", 4),
                                 f"JBallin/claude-review-runtime/.github/workflows/{entrypoint}@{PIN}")
                self.assertIn("\npermissions: {}\n", text)
                for forbidden in ("concurrency:", "secrets: inherit", "workflow_dispatch:",
                                  "pull_request_target:", "workflow_run:", "with:"):
                    self.assertNotIn(forbidden, text)
                self.assertNotIn("steps:", "\n".join(called))
                self.assertNotIn("branches:", text, "Stacked PRs must remain eligible")

    def test_secret_and_permissions_are_limited_to_each_job_role(self):
        review_permissions = {"contents": "read", "pull-requests": "write",
                              "checks": "write", "issues": "write", "id-token": "write"}
        for path, name, _ in CALLERS:
            with self.subTest(caller=path.name):
                called = w.job(path, name)
                expected = review_permissions if path != STATUS else {
                    "contents": "read", "pull-requests": "write", "issues": "write"}
                self.assertEqual(permissions(w.block(called, "permissions", 4), 6), expected)
                if path == STATUS:
                    self.assertNotIn("secrets:", path.read_text())
                else:
                    self.assertEqual(w.block(called, "secrets", 4), [
                        "      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"])
        gate = w.job(MANUAL, "eligibility")
        self.assertEqual(permissions(w.block(gate, "permissions", 4), 6), {"pull-requests": "read"})
        self.assertEqual(w.scalar(gate, "timeout-minutes", 4), "2")
        self.assertNotIn("uses:", "\n".join(gate))
        self.assertEqual(w.scalar(w.job(MANUAL, "review"), "needs", 4), "eligibility")

    def test_event_sets_do_not_start_paid_reviews_on_head_updates(self):
        expected = {AUTOMATIC: ["pull_request", "[opened, ready_for_review]"],
                    MANUAL: ["issue_comment", "[created]", "pull_request_review_comment", "[created]"],
                    STATUS: ["pull_request", "[synchronize, edited]"]}
        for path, events in expected.items():
            trigger = w.block(w.lines_of(path), "on", 0)
            self.assertEqual([line.strip().removesuffix(":").removeprefix("types: ")
                              for line in trigger if line.strip()], events)

    def test_automatic_and_status_eligibility(self):
        review = w.folded(w.job(AUTOMATIC, "review"), "if", 4)
        status = w.folded(w.job(STATUS, "status"), "if", 4)
        for state, draft, actor, repo, allowed_review, allowed_status in (
            ("open", False, "owner", w.REPO, True, True),
            ("open", True, "owner", w.REPO, False, True),
            ("open", False, "dependabot[bot]", w.REPO, False, True),
            ("closed", False, "owner", w.REPO, False, False),
            ("open", False, "owner", "other/fork", False, False),
        ):
            event = w.pull_request_event(draft=draft, actor=actor, head_repo=repo)
            event["event"]["pull_request"]["state"] = state
            with self.subTest(state=state, draft=draft, actor=actor, repo=repo):
                self.assertEqual(bool(w.evaluate(review, event)), allowed_review)
                self.assertEqual(bool(w.evaluate(status, event)), allowed_status)

    def test_manual_requires_trusted_exact_command_on_pr_comments(self):
        gate = w.folded(w.job(MANUAL, "eligibility"), "if", 4)
        for factory in (w.issue_comment_event, w.review_comment_event):
            for association in ("OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR", "NONE", "FIRST_TIMER"):
                for body in ("/claude-review", "/CLAUDE-REVIEW", " /claude-review", "/claude-review\n",
                             "/claude-review now", "`/claude-review`", "> /claude-review",
                             "@claude review", "Please @CLAUDE review this", "ordinary text"):
                    with self.subTest(event=factory.__name__, association=association, body=body):
                        expected = association in ("OWNER", "MEMBER", "COLLABORATOR") and body.lower() == "/claude-review"
                        self.assertEqual(bool(w.evaluate(gate, factory(body=body, association=association))), expected)
        self.assertFalse(w.evaluate(gate, w.issue_comment_event(body="/claude-review", on_pr=False)))
        review = w.scalar(w.job(MANUAL, "review"), "if", 4)
        for result in ("true", "false", "", None):
            self.assertEqual(w.Evaluator(review, {"needs": {"eligibility": {
                "outputs": {"eligible": result}}}}).evaluate(), result == "true")


class ManualEligibilityTests(unittest.TestCase):
    def run_gate(self, payload, failed=False):
        script = w.run_block(w.steps(w.job(MANUAL, "eligibility"))["Check PR eligibility"])
        self.assertNotIn("${{", script, "Event text must be passed as environment data")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = root / "gh"
            stub.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                            "from pathlib import Path\n"
                            "Path(os.environ['GH_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
                            "print(os.environ['PR_JSON'])\n"
                            "sys.exit(int(os.environ['GH_EXIT']))\n")
            stub.chmod(0o755)
            output, log, sentinel = root / "output", root / "log", root / "injected"
            raw = payload if isinstance(payload, str) else json.dumps(payload)
            # The unused title/body still exercise the actual shell's handling of
            # untrusted API output, without executing any PR-controlled content.
            raw = raw.replace("HOSTILE", f"$(touch {sentinel}) `touch {sentinel}`")
            environment = {"PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                           "REPO": w.REPO, "PR_NUMBER": "7", "PR_JSON": raw,
                           "GH_LOG": str(log), "GH_EXIT": "1" if failed else "0",
                           "GITHUB_OUTPUT": str(output)}
            result = subprocess.run(["bash", "-eo", "pipefail", "-c", script],
                                    cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(json.loads(log.read_text()), ["api", f"repos/{w.REPO}/pulls/7"])
            self.assertFalse(sentinel.exists())
            return result, output.read_text() if output.exists() else ""

    def test_gate_accepts_main_and_stacked_prs_without_executing_metadata(self):
        for base in ("main", "issue-2-manual-review-ux"):
            result, output = self.run_gate({"state": "open", "draft": False,
                "head": {"repo": {"full_name": w.REPO}}, "base": {"ref": base},
                "title": "HOSTILE", "body": "HOSTILE"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output, "eligible=true\n")

    def test_gate_rejects_closed_draft_fork_and_missing_metadata(self):
        for state, draft, repo in (("closed", False, w.REPO), ("open", True, w.REPO),
                                   ("open", False, "other/fork"), ("open", None, None)):
            result, output = self.run_gate({"state": state, "draft": draft,
                                          "head": {"repo": {"full_name": repo}}})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output, "eligible=false\n")

    def test_failed_lookup_and_malformed_response_never_grant_eligibility(self):
        for raw, failed in (("{", False), ("[]", False), ("", True),
                            ('{"state":"open","draft":false,"head":{"repo":{"full_name":"example/repository"}}}', True)):
            result, output = self.run_gate(raw, failed=failed)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
