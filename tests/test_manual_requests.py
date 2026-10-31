"""Strict manual admission and the actual checkout-free usage workflow, offline."""
import json
import ast
import inspect
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_claude_review_workflows as w

check = w.check
UNSET = object()


def classify(body):
    script = w.run_block(w.steps(w.job(w.MANUAL, "request"))["Classify manual review request"])
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "outputs"
        result = subprocess.run(["bash", "-eo", "pipefail", "-c", script],
                                env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output),
                                     "TRIGGER_COMMENT": body}, capture_output=True, text=True)
        values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
        return result, values


def usage_probe(body="/claude-review opus invalid", *, event=None, permission=UNSET, pr=UNSET,
                fail="", classification_failure=False):
    event = event or w.issue_comment_event(body=body)
    gate = w.scalar(w.job(w.MANUAL, "request"), "if", 4)
    if not w.evaluate(gate, event):
        return None, {}, []
    result, values = classify(event["event"]["comment"]["body"])
    if classification_failure:
        values = {}
    if result.returncode or values.get("classification") != "invalid":
        return result, values, []
    script = w.run_block(w.steps(w.job(w.MANUAL, "request"))["Explain invalid manual request"])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stub, log = root / "gh", root / "calls"
        stub.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
path = args[1]
method = args[args.index("--method") + 1] if "--method" in args else "GET"
body = json.loads(sys.stdin.read()) if "--input" in args else None
kind = "POST" if method == "POST" else "PERMISSION" if path.endswith("/permission") else "PR"
with open(os.environ["GH_LOG"], "a") as handle:
    handle.write(json.dumps(dict(method=method, path=path, body=body)) + "\\n")
if kind == os.environ.get("FAIL"):
    print("synthetic unavailable response", file=sys.stderr)
    sys.exit(1)
print(os.environ.get(kind, "{}"))
''')
        stub.chmod(0o755)
        commenter = event["event"]["comment"]["user"]["login"]
        environment = {"PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}", "GH_LOG": str(log),
                       "REPO": w.REPO, "PR_NUMBER": "7", "COMMENTER": commenter,
                       "RESPONSE_JSON": values["response"], "FAIL": fail,
                       "PERMISSION": json.dumps(permission if permission is not UNSET else
                                                {"permission": "write", "user": {"login": commenter}}),
                       "PR": json.dumps(pr if pr is not UNSET else
                                        {"state": "open", "draft": False,
                                         "head": {"repo": {"full_name": w.REPO}}})}
        result = subprocess.run(["bash", "-eo", "pipefail", "-c", script], env=environment,
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, values, calls


class ManualRequestTests(unittest.TestCase):
    def test_trusted_inline_parser_and_response_match_helper_source(self):
        script = w.run_block(w.steps(w.job(w.MANUAL, "request"))["Classify manual review request"])
        source = script.split("\n", 1)[1].rsplit("PY_REQUEST", 1)[0]
        tree = ast.parse(source)
        for name in ("classify_manual_request", "manual_usage_response"):
            inline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            helper = ast.parse(inspect.getsource(getattr(check, name))).body[0]
            self.assertEqual(ast.dump(inline), ast.dump(helper))

    def test_all_short_forms_and_line_placement_match_real_workflow(self):
        for command, selected in check.MANUAL_COMMANDS.items():
            for body in (command, command.upper(), " \t" + command + "\t ", "\u00a0" + command.replace(" ", "\u00a0") + "\u2003",
                         "Context\n" + command.replace(" ", "\t") + "\r\n@codex review",
                         "```text\n/claude-review invalid\n```\n" + command,
                         "> /claude-review invalid\n" + command):
                with self.subTest(body=body):
                    parsed = check.classify_manual_request(body)
                    self.assertEqual(parsed["status"], "valid")
                    self.assertEqual((parsed["requested_model"], parsed["requested_effort"]), selected)
                    result, values = classify(body)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(values, {"classification": "valid", "response": '""'})

    def test_ignored_examples_never_become_requests(self):
        examples = ("", "please /claude-review", "Review /claude-review opus", "/claude-reviewing",
                    "`/claude-review`", "> /claude-review", " > /claude-review high",
                    "```text\n/claude-review\n```", "~~~\n/claude-review\n~~~",
                    "```\n/claude-review\n~~", "~~~\n```\n/claude-review\n~~~")
        for body in examples:
            with self.subTest(body=body):
                self.assertEqual(check.classify_manual_request(body)["status"], "ignored")
                result, values = classify(body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["classification"], "ignored")
                with self.assertRaises(ValueError):
                    check.manual_request(body)

    def test_invalid_entire_lines_have_no_partial_selection_and_fixed_usage(self):
        invalid = ("invalid", "opus invalid", "invalid high", "high opus", "opus opus", "high max",
                   "effort high", "opus effort high", "--model opus", "opus --effort high",
                   "opus high trailing words", "please review this", "$(id) high", "haiku; echo bad")
        for arguments in invalid:
            body = "/claude-review " + arguments
            with self.subTest(body=body):
                parsed = check.classify_manual_request(body)
                self.assertEqual(parsed, dict(status="invalid", reason="arguments", requested_model="", requested_effort=""))
                result, values = classify(body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["classification"], "invalid")
                self.assertEqual(json.loads(values["response"]), check.manual_usage_response("arguments"))
                with self.assertRaises(ValueError):
                    check.resolve_configuration(body, "haiku", "low")

    def test_multiple_candidates_reject_even_if_one_is_valid(self):
        for body in ("/claude-review\n/claude-review opus", "/claude-review invalid\n/claude-review",
                     "/claude-review\n/claude-review"):
            parsed = check.classify_manual_request(body)
            self.assertEqual(parsed["reason"], "multiple")
            _, values = classify(body)
            self.assertEqual(json.loads(values["response"]), check.manual_usage_response("multiple"))

    def test_multiline_inline_code_never_admits_or_responds_to_examples(self):
        examples = [f"Example {ticks}start\n/claude-review opus high\nend{ticks}" for ticks in ("`", "``", "```")]
        examples += ["`example\n/claude-review opus high\nend``",
                     "``example\n`nested`\n/claude-review opus high\nend```",
                     "prose `\n/claude-review opus high",
                     "> `quoted example\n/claude-review opus high\nend`",
                     "Literal \\``example\n/claude-review opus high\nend`"]
        for body in examples:
            with self.subTest(body=body):
                self.assertEqual(check.classify_manual_request(body)["status"], "ignored")
                result, values, calls = usage_probe(body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["classification"], "ignored")
                self.assertEqual(calls, [])
                self.assertFalse(w.Evaluator(w.scalar(w.job(w.MANUAL, "start-check"), "if", 4),
                                            {"needs": {"request": {"outputs": values}}}).evaluate())

    def test_inline_delimiter_boundaries_and_real_commands_after_examples(self):
        examples = [f"Example {ticks}start\n/claude-review opus high\nend{ticks}\n/claude-review haiku low"
                    for ticks in ("`", "``", "```")]
        examples += ["``example\n`/claude-review opus high`\nend``\n/claude-review haiku low",
                     "Literal \\`\n/claude-review haiku low",
                     "Example `same line`\n/claude-review haiku low",
                     "> `quoted example`\n/claude-review haiku low"]
        for body in examples:
            with self.subTest(body=body):
                parsed = check.classify_manual_request(body)
                self.assertEqual(parsed["status"], "valid")
                self.assertEqual((parsed["requested_model"], parsed["requested_effort"]), ("haiku", "low"))
                result, values = classify(body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["classification"], "valid")

    def test_feedback_is_one_guarded_post_without_review_operations(self):
        for event in (w.issue_comment_event(body="/claude-review invalid"),
                      w.review_comment_event(body="/claude-review invalid")):
            event["actor"] = "rerun-admin"
            result, _, calls = usage_probe(event=event)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([call["method"] for call in calls], ["GET", "GET", "POST"])
            self.assertIn("/collaborators/comment-author/permission", calls[0]["path"])
            self.assertEqual(calls[-1]["path"], f"repos/{w.REPO}/issues/7/comments")
            self.assertEqual(calls[-1]["body"], {"body": check.manual_usage_response("arguments")})
            self.assertNotIn("rerun-admin", json.dumps(calls))
            self.assertNotIn("claude-review-runtime:", json.dumps(calls))

    def test_managed_user_login_receives_feedback_with_original_commenter_permission(self):
        for login in ("reviewer_acme", "a" * 34 + "_acme"):
            for factory in (w.issue_comment_event, w.review_comment_event):
                with self.subTest(login=login, event=factory.__name__):
                    event = factory(body="/claude-review invalid")
                    event["actor"] = "rerun-admin"
                    event["event"]["comment"]["user"]["login"] = login
                    result, _, calls = usage_probe(event=event)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual([call["method"] for call in calls], ["GET", "GET", "POST"])
                    self.assertEqual(calls[0]["path"], f"repos/{w.REPO}/collaborators/{login}/permission")
                    self.assertEqual(calls[-1]["body"], {"body": check.manual_usage_response("arguments")})
                    self.assertNotIn("rerun-admin", json.dumps(calls))

    def test_managed_user_login_retains_permission_identity_and_length_limits(self):
        login = "reviewer_acme"
        event = w.issue_comment_event(body="/claude-review invalid")
        event["event"]["comment"]["user"]["login"] = login
        for permission in ({"permission": "read", "user": {"login": login}},
                           {"permission": "none", "user": {"login": login}},
                           {"permission": "write", "user": {"login": "rerun-admin"}}):
            with self.subTest(permission=permission):
                result, _, calls = usage_probe(event=event, permission=permission)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([call["method"] for call in calls], ["GET"])
                self.assertEqual(calls[0]["path"], f"repos/{w.REPO}/collaborators/{login}/permission")
        event["event"]["comment"]["user"]["login"] = "a" * 35 + "_acme"
        result, _, calls = usage_probe(event=event)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_event_and_human_gate_excludes_bots_edits_untrusted_and_non_prs(self):
        base = w.issue_comment_event(body="/claude-review invalid")
        import copy
        events = []
        for key, value in (("action", "edited"),):
            event = copy.deepcopy(base)
            event["event"][key] = value
            events.append(event)
        for user_type in ("Bot", "Organization", ""):
            event = copy.deepcopy(base)
            event["event"]["comment"]["user"]["type"] = user_type
            events.append(event)
        events += [w.issue_comment_event(body="/claude-review invalid", association="NONE"),
                   w.issue_comment_event(body="/claude-review invalid", on_pr=False)]
        for event in events:
            self.assertEqual(usage_probe(event=event)[2], [])

    def test_permission_and_live_pr_fail_closed(self):
        for permission in (None, {}, [], {"permission": "read"}, {"permission": "maintain"},
                           {"permission": "admin", "user": {"login": "rerun-admin"}},
                           {"permission": "write", "user": None}):
            result, _, calls = usage_probe(permission=permission)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("eligibility could not be verified", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(any(c["method"] == "POST" for c in calls))
        for change in ({"state": "closed"}, {"draft": True}, {"draft": "false"},
                       {"head": {"repo": {"full_name": "other/fork"}}}, {"head": None}):
            pr = {"state": "open", "draft": False, "head": {"repo": {"full_name": w.REPO}}, **change}
            result, _, calls = usage_probe(pr=pr)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(c["method"] == "POST" for c in calls))
        for pr in (None, [], {}):
            result, _, calls = usage_probe(pr=pr)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("eligibility could not be verified", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(any(c["method"] == "POST" for c in calls))
        for permission in ("write", "admin"):
            result, _, calls = usage_probe(permission={"permission": permission, "role_name": "maintain",
                                                      "user": {"login": "comment-author"}})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[-1]["method"], "POST")

    def test_failures_ignored_and_valid_requests_never_start_usage_or_emergency_review(self):
        for body in ("please /claude-review", "/claude-review", "/claude-review invalid"):
            for failed in (False, True):
                _, values, calls = usage_probe(body, classification_failure=failed)
                valid = values.get("classification") == "valid"
                start = w.Evaluator(w.scalar(w.job(w.MANUAL, "start-check"), "if", 4),
                                    {"needs": {"request": {"outputs": values}}}).evaluate()
                self.assertEqual(bool(start), valid)
                if not valid:
                    publication = w.scalar(w.job(w.MANUAL, "publish-status"), "if", 4)
                    self.assertFalse(w.Evaluator(publication, {"needs": {"start-check": {
                        "result": "skipped", "outputs": {"tooling_unavailable": "true"}}}}).evaluate())
                if body != "/claude-review invalid" or failed:
                    self.assertEqual(calls, [])
        for failure in ("PERMISSION", "PR", "POST"):
            result, _, calls = usage_probe(fail=failure)
            self.assertNotEqual(result.returncode, 0)
            self.assertLessEqual(sum(c["method"] == "POST" for c in calls), 1)

    def test_publication_requires_successful_valid_classification_even_without_tooling(self):
        condition = w.scalar(w.job(w.MANUAL, "publish-status"), "if", 4)
        for result in ("success", "failure", "skipped"):
            for classification in ("valid", "invalid", "ignored", ""):
                context = {"needs": {
                    "request": {"result": result, "outputs": {"classification": classification}},
                    "start-check": {"result": "failure", "outputs": {
                        "request_recognized": "true", "tooling_unavailable": "true", "selection_accepted": "false"}}}}
                with self.subTest(result=result, classification=classification):
                    self.assertEqual(bool(w.Evaluator(condition, context).evaluate()),
                                     result == "success" and classification == "valid")

    def test_classifier_failure_emits_no_admission_outputs(self):
        script = w.run_block(w.steps(w.job(w.MANUAL, "request"))["Classify manual review request"])
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["bash", "-eo", "pipefail", "-c", script],
                                    env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": directory,
                                         "TRIGGER_COMMENT": "/claude-review"}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("IsADirectoryError", result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_request_job_has_no_checkout_model_secret_check_or_ownership_path(self):
        job = w.job(w.MANUAL, "request")
        self.assertEqual(w.block(job, "permissions", 4), ["      pull-requests: write"])
        source = "\n".join(job)
        for forbidden in ("actions/checkout", "uses:", "CLAUDE_CODE_OAUTH_TOKEN", "checks:", "id-token:",
                          "presentation-owner", "selection_config", "/check-runs", "/reactions"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
