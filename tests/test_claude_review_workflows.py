"""Structural tests for the Claude review workflows' trust and queue gates.

The eligibility predicates and concurrency groups are evaluated from the
workflow text itself with a small evaluator for the GitHub Actions expression
subset they use, so these tests fail if the YAML changes in a way that alters
who can start a review or which queue a run joins.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
AUTOMATIC = WORKFLOWS / "claude-review.yml"
MANUAL = WORKFLOWS / "claude.yml"
STALE = WORKFLOWS / "claude-review-status.yml"
REPO = "example/repository"
SCRIPT = WORKFLOWS.parent.parent / "actions" / "review-helper" / "claude_review_check.py"

spec = importlib.util.spec_from_file_location("claude_review_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

# Runner setup, packaged helper preparation, and artifact transfer.
JOB_OVERHEAD_SECONDS = 90
# The helper commands each job runs; their worst cases come from check.WORST_CASE.
JOB_COMMANDS = {"start-check": ["create", "snapshot"], "publish-status": ["finalize"]}


def worst_case_seconds(command):
    if command in ("status", "stale", "completion", "admit-manual"):
        return check.PRESENTATION_SECONDS
    calls, retried_operations = check.WORST_CASE[command]
    backoff = sum(check.RETRY_DELAY_SECONDS * 2**attempt for attempt in range(check.ATTEMPTS - 1))
    return calls * check.GH_TIMEOUT_SECONDS + retried_operations * backoff


# --- Minimal YAML block extraction (the workflows use a small, stable subset).


def lines_of(path):
    return path.read_text(encoding="utf-8").splitlines()


def block(lines, header, indent):
    """Lines belonging to a `header:` mapping key at the given indentation."""
    prefix = " " * indent
    start = lines.index(f"{prefix}{header}:")
    body = []
    for line in lines[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return body


def job(path, name):
    return block(block(lines_of(path), "jobs", 0), name, 2)


def folded(lines, key, indent):
    """The value of a `key: >-` folded scalar whose lines share one indentation."""
    prefix = " " * indent
    start = lines.index(f"{prefix}{key}: >-")
    parts = []
    for line in lines[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        parts.append(line.strip())
    return " ".join(part for part in parts if part)


def steps(job_lines):
    """Map each step's name to its lines."""
    named, current = {}, None
    for line in job_lines:
        match = re.match(r"^      - name: (.+)$", line)
        if match:
            current = named.setdefault(match.group(1), [])
        if current is not None:
            current.append(line)
    return named


def run_block(step_lines):
    """The literal `run: |` script of a step, dedented."""
    start = step_lines.index("        run: |")
    return textwrap.dedent("\n".join(step_lines[start + 1 :])) + "\n"


def scalar(lines, key, indent):
    pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}: (.+)$")
    matches = [m.group(1) for m in map(pattern.match, lines) if m]
    return matches[0] if matches else None


def unwrap(expression):
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        return expression[3:-2].strip()
    return expression


# --- Evaluator for the GitHub Actions expression subset used above.

TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+)|(?P<op>&&|\|\||==|!=|!|\(|\)|,)"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_\-]*(?:\.[A-Za-z_][A-Za-z0-9_\-]*)*))"
)


def tokenize(text):
    tokens, index = [], 0
    while index < len(text):
        if text[index:].strip() == "":
            break
        match = TOKEN.match(text, index)
        if not match or match.end() == index:
            raise ValueError(f"cannot tokenize {text[index:]!r}")
        index = match.end()
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
    return tokens


def truthy(value):
    return value not in (None, False, 0, "") and not (isinstance(value, float) and value == 0)


def to_number(value):
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value) if value.strip() else 0.0
        except ValueError:
            return float("nan")
    return float("nan")


def loose_equal(left, right):
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    if type(left) is type(right) and not isinstance(left, (dict, list)):
        return left == right
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return left is right
    return to_number(left) == to_number(right)


def to_string(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


FUNCTIONS = {
    "contains": lambda search, item: (
        any(loose_equal(entry, item) for entry in search)
        if isinstance(search, list)
        else to_string(item).casefold() in to_string(search).casefold()
    ),
    "fromJSON": json.loads,
    "format": lambda template, *args: re.sub(
        r"\{(\d+)\}", lambda m: to_string(args[int(m.group(1))]), template
    ),
}


class Evaluator:
    def __init__(self, text, context):
        self.tokens = tokenize(text)
        self.position = 0
        self.context = context

    def peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else (None, None)

    def take(self, expected=None):
        token = self.peek()
        if expected is not None and token[1] != expected:
            raise ValueError(f"expected {expected!r}, got {token!r}")
        self.position += 1
        return token

    def evaluate(self):
        value = self.or_expression()
        if self.position != len(self.tokens):
            raise ValueError(f"unexpected trailing token {self.peek()!r}")
        return value

    def or_expression(self):
        value = self.and_expression()
        while self.peek()[1] == "||":
            self.take()
            right = self.and_expression()
            value = value if truthy(value) else right
        return value

    def and_expression(self):
        value = self.comparison()
        while self.peek()[1] == "&&":
            self.take()
            right = self.comparison()
            value = right if truthy(value) else value
        return value

    def comparison(self):
        value = self.unary()
        while self.peek()[1] in ("==", "!="):
            operator = self.take()[1]
            right = self.unary()
            equal = loose_equal(value, right)
            value = equal if operator == "==" else not equal
        return value

    def unary(self):
        if self.peek()[1] == "!":
            self.take()
            return not truthy(self.unary())
        return self.primary()

    def primary(self):
        kind, text = self.take()
        if text == "(":
            value = self.or_expression()
            self.take(")")
            return value
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text)
        if kind == "ident":
            if self.peek()[1] == "(":
                self.take("(")
                args = []
                while self.peek()[1] != ")":
                    args.append(self.or_expression())
                    if self.peek()[1] == ",":
                        self.take(",")
                self.take(")")
                return FUNCTIONS[text](*args)
            if text in ("true", "false"):
                return text == "true"
            if text == "null":
                return None
            value = self.context
            for part in text.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            return value
        raise ValueError(f"unexpected token {text!r}")


def evaluate(expression, github, steps=None):
    return Evaluator(unwrap(expression), {"github": github, "steps": steps or {}}).evaluate()


# --- Synthetic event payloads.


def pull_request_event(*, draft=False, actor="example-user", head_repo=REPO, number=7):
    return {
        "event_name": "pull_request",
        "actor": actor,
        "repository": REPO,
        "run_id": 9001,
        "event": {
            "pull_request": {
                "number": number,
                "draft": draft,
                "head": {"repo": {"full_name": head_repo}},
            }
        },
    }


def issue_comment_event(*, body="/claude-review", association="OWNER", on_pr=True, number=7):
    issue = {"number": number}
    if on_pr:
        issue["pull_request"] = {"url": f"https://api.github.com/repos/{REPO}/pulls/{number}"}
    return {
        "event_name": "issue_comment",
        "actor": "someone",
        "repository": REPO,
        "run_id": 9002,
        "event": {"comment": {"body": body, "author_association": association}, "issue": issue},
    }


def review_comment_event(*, body="/claude-review", association="MEMBER", number=7):
    return {
        "event_name": "pull_request_review_comment",
        "actor": "someone",
        "repository": REPO,
        "run_id": 9003,
        "event": {
            "comment": {"body": body, "author_association": association},
            "pull_request": {"number": number},
        },
    }


def start_check_predicate(path):
    return folded(job(path, "start-check"), "if", 4)


def concurrency_group(path):
    lines = block(lines_of(path), "concurrency", 0)
    value = scalar(lines, "group", 2)
    return folded(lines, "group", 2) if value == ">-" else value


class EligibilityPredicateTests(unittest.TestCase):
    def test_automatic_review_runs_only_for_ready_same_repo_non_dependabot_prs(self):
        predicate = start_check_predicate(AUTOMATIC)
        self.assertTrue(evaluate(predicate, pull_request_event()))
        self.assertFalse(evaluate(predicate, pull_request_event(draft=True)))
        self.assertFalse(evaluate(predicate, pull_request_event(actor="dependabot[bot]")))
        self.assertFalse(evaluate(predicate, pull_request_event(head_repo="someone/fork")))

    def test_manual_review_requires_trusted_standalone_command_on_a_pr(self):
        predicate = start_check_predicate(MANUAL)
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            self.assertTrue(evaluate(predicate, issue_comment_event(association=association)))
        self.assertTrue(evaluate(predicate, review_comment_event()))
        for association in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "NONE"):
            self.assertFalse(evaluate(predicate, issue_comment_event(association=association)))
            self.assertFalse(evaluate(predicate, review_comment_event(association=association)))
        self.assertFalse(evaluate(predicate, issue_comment_event(body="looks good")))
        self.assertFalse(evaluate(predicate, issue_comment_event(on_pr=False)))

    def test_manual_command_matches_only_the_entire_body_case_insensitively(self):
        for factory in (issue_comment_event, review_comment_event):
            for body in ("/claude-review", "/CLAUDE-REVIEW", "/Claude-Review"):
                event = factory(body=body)
                self.assertTrue(evaluate(start_check_predicate(MANUAL), event))
                self.assertEqual(evaluate(concurrency_group(MANUAL), event),
                                 f"claude-review-state-{REPO}-7")
            for body in ("@claude", "@claude review", "/claude-review now",
                         "please /claude-review", "`/claude-review`", "> /claude-review",
                         " /claude-review", "/claude-review ", "\t/claude-review",
                         "/claude-review\n", "\n/claude-review\n", "", None):
                with self.subTest(event=factory.__name__, body=body):
                    event = factory(body=body)
                    self.assertFalse(evaluate(start_check_predicate(MANUAL), event))
                    self.assertEqual(evaluate(concurrency_group(MANUAL), event),
                                     f"claude-review-ineligible-{event['run_id']}")


    def test_reusable_entrypoints_reject_unrelated_caller_event_names(self):
        for path, event in ((AUTOMATIC, pull_request_event()), (MANUAL, issue_comment_event())):
            for event_name in ("workflow_dispatch", "push", "schedule"):
                with self.subTest(workflow=path.name, event_name=event_name):
                    unrelated = {**event, "event_name": event_name}
                    self.assertFalse(evaluate(start_check_predicate(path), unrelated))
                    self.assertIn("ineligible", evaluate(concurrency_group(path), unrelated))


class ConcurrencyGroupTests(unittest.TestCase):
    def test_group_predicate_is_textually_identical_to_start_check_gate(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                predicate = start_check_predicate(path)
                self.assertIn(f"({predicate}) && format(", unwrap(concurrency_group(path)))

    def test_eligible_runs_share_one_queue_across_both_workflows(self):
        expected = f"claude-review-state-{REPO}-7"
        self.assertEqual(evaluate(concurrency_group(AUTOMATIC), pull_request_event()), expected)
        self.assertEqual(evaluate(concurrency_group(MANUAL), issue_comment_event()), expected)
        self.assertEqual(evaluate(concurrency_group(MANUAL), review_comment_event()), expected)

    def test_consumers_with_the_same_pr_number_do_not_share_a_queue(self):
        for path, event in ((AUTOMATIC, pull_request_event()), (MANUAL, issue_comment_event())):
            other_consumer = {**event, "repository": "example/other-repository"}
            if path == AUTOMATIC:
                other_consumer = json.loads(json.dumps(other_consumer))
                other_consumer["event"]["pull_request"]["head"]["repo"]["full_name"] = other_consumer["repository"]
            with self.subTest(workflow=path.name):
                self.assertNotEqual(evaluate(concurrency_group(path), event),
                                    evaluate(concurrency_group(path), other_consumer))

    def test_ineligible_runs_get_a_per_run_group(self):
        cases = [
            (AUTOMATIC, pull_request_event(draft=True)),
            (AUTOMATIC, pull_request_event(actor="dependabot[bot]")),
            (AUTOMATIC, pull_request_event(head_repo="someone/fork")),
            (MANUAL, issue_comment_event(association="NONE")),
            (MANUAL, issue_comment_event(body="thanks")),
            (MANUAL, issue_comment_event(on_pr=False)),
        ]
        for path, event in cases:
            with self.subTest(workflow=path.name, event=event):
                self.assertEqual(
                    evaluate(concurrency_group(path), event),
                    f"claude-review-ineligible-{event['run_id']}",
                )

    def test_queue_keeps_pending_runs_without_cancelling_in_progress_ones(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                concurrency = block(lines_of(path), "concurrency", 0)
                self.assertEqual(scalar(concurrency, "cancel-in-progress", 2), "false")
                self.assertEqual(scalar(concurrency, "queue", 2), "max")


class JobStructureTests(unittest.TestCase):
    def test_presentation_diagnostic_is_carried_only_between_trusted_jobs(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                outputs = block(job(path, "start-check"), "outputs", 4)
                self.assertEqual(scalar(outputs, "presentation_outcome", 6),
                                 "${{ steps.check.outputs.presentation_outcome }}")
                publish = steps(job(path, "publish-status"))["Publish Claude Review result"]
                self.assertEqual(scalar(block(publish, "env", 8), "PRESENTATION_OUTCOME", 10),
                                 "${{ needs.start-check.outputs.presentation_outcome }}")
                self.assertNotIn("PRESENTATION_OUTCOME", "\n".join(job(path, "review")))

    def test_review_runs_only_after_a_successful_start_check(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                review = job(path, "review")
                self.assertEqual(scalar(review, "needs", 4), "start-check")
                self.assertIsNone(scalar(review, "if", 4))

    def test_publish_status_never_runs_for_a_skipped_start_check(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                publish = job(path, "publish-status")
                self.assertEqual(scalar(publish, "needs", 4), "[start-check, review]")
                self.assertEqual(
                    scalar(publish, "if", 4), "always() && needs.start-check.result != 'skipped'"
                )

    def test_check_and_pr_writes_are_limited_to_trusted_publication_jobs(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                top_level = block(lines_of(path), "permissions", 0)
                expected = ["contents: read", "pull-requests: read", "id-token: write"]
                self.assertEqual([line.strip() for line in top_level if line.strip()], expected)
                review = job(path, "review")
                self.assertEqual([line.strip() for line in block(review, "permissions", 4) if line.strip()], expected)
                for name in ("start-check", "publish-status"):
                    permissions = block(job(path, name), "permissions", 4)
                    self.assertEqual(
                        [line.strip() for line in permissions if line.strip()],
                        ["contents: read", "pull-requests: write", "checks: write", "issues: write"],
                    )
                    self.assertNotIn("anthropics/claude-code-action", "\n".join(job(path, name)))

    def test_check_run_is_created_before_fallible_review_input_capture(self):
        cases = {
            AUTOMATIC: ["Capture presentation start", "Check review tooling", "Start Claude Review check", "Fetch PR review inputs"],
            MANUAL: [
                "Resolve PR under review",
                "Capture presentation start",
                "Check review tooling",
                "Verify manual base freshness",
                "Start Claude Review check",
                "Capture PR review snapshot",
            ],
        }
        for path, expected_order in cases.items():
            with self.subTest(workflow=path.name):
                names = list(steps(job(path, "start-check")))
                self.assertEqual(names[: len(expected_order)], expected_order)
        resolve = "\n".join(steps(job(MANUAL, "start-check"))["Resolve PR under review"])
        self.assertNotIn("compare", resolve)

    def test_fork_evidence_failures_cannot_skip_the_manual_review(self):
        named = steps(job(MANUAL, "start-check"))
        required = ["Resolve PR under review", "Verify manual base freshness", "Capture PR review snapshot", "Upload review snapshot"]
        evidence_only = [
            "Check review tooling",
            "Start Claude Review check",
            "Record existing Claude review comments",
            "Upload pre-review comment evidence",
        ]
        self.assertEqual(sorted(named), sorted(required + evidence_only + ["Capture presentation start"]))
        self.assertEqual(scalar(named["Capture presentation start"], "continue-on-error", 8), "true")
        for name in required:
            with self.subTest(step=name):
                self.assertIsNone(scalar(named[name], "continue-on-error", 8))
        for name in evidence_only:
            with self.subTest(step=name):
                expression = scalar(named[name], "continue-on-error", 8)
                fork = {"resolve": {"outputs": {"is_fork": "true"}}}
                same_repo = {"resolve": {"outputs": {"is_fork": "false"}}}
                self.assertIs(evaluate(expression, {}, fork), True)
                self.assertIs(evaluate(expression, {}, same_repo), False)
                self.assertIs(evaluate(expression, {}, {}), False)

    def test_same_repo_only_automatic_review_keeps_every_start_step_fail_closed(self):
        for name, lines in steps(job(AUTOMATIC, "start-check")).items():
            if name == "Capture presentation start":
                self.assertEqual(scalar(lines, "continue-on-error", 8), "true")
                continue
            with self.subTest(step=name):
                self.assertIsNone(scalar(lines, "continue-on-error", 8))

    def test_tooling_failure_reaches_a_trusted_fallback_instead_of_skipping_publication(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                named = steps(job(path, "publish-status"))
                readiness = named["Check review tooling"]
                self.assertEqual(scalar(readiness, "id", 8), "tooling-ready")
                self.assertEqual(scalar(readiness, "continue-on-error", 8), "true")
                self.assertEqual(
                    scalar(named["Publish Claude Review result"], "if", 8),
                    "steps.tooling-ready.outcome == 'success'",
                )
                fallback = named["Record failure without review tooling"]
                self.assertEqual(scalar(fallback, "if", 8), "always() && steps.tooling-ready.outcome != 'success'")
                self.assertIsNotNone(scalar(fallback, "timeout-minutes", 8))
                self.assertIsNone(scalar(fallback, "uses", 8))
                self.assertIn("CHECK_RUN_ID: ${{ needs.start-check.outputs.check_run_id }}", "\n".join(fallback))

    def test_publication_still_runs_when_the_evidence_download_fails(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                named = steps(job(path, "publish-status"))
                download = named["Download pre-review comment evidence"]
                self.assertEqual(scalar(download, "continue-on-error", 8), "true")
                # Only the tooling outcome gates publication, not the download.
                self.assertNotIn("download", scalar(named["Publish Claude Review result"], "if", 8))

    def test_job_timeouts_cover_the_worst_case_retry_budget(self):
        for path in (AUTOMATIC, MANUAL):
            for name, commands in JOB_COMMANDS.items():
                with self.subTest(workflow=path.name, job=name):
                    needed = sum(worst_case_seconds(command) for command in commands)
                    needed += worst_case_seconds("status")
                    if path == MANUAL and name == "publish-status":
                        needed += worst_case_seconds("completion")
                    if path == MANUAL and name == "start-check":
                        needed += worst_case_seconds("admit-manual")
                    timeout = int(scalar(job(path, name), "timeout-minutes", 4)) * 60
                    self.assertLessEqual(needed + JOB_OVERHEAD_SECONDS, timeout)
                    for command in commands:
                        self.assertIn(f'"$REVIEW_HELPER" {command}', "\n".join(job(path, name)))

    def test_completion_notice_is_enabled_only_in_manual_trusted_finalization(self):
        for path in (AUTOMATIC, MANUAL):
            text = path.read_text()
            self.assertEqual(text.count('MANUAL_COMPLETION_ENABLED: "true"'),
                             1 if path == MANUAL else 0)
            for name in ("start-check", "review"):
                self.assertNotIn("MANUAL_COMPLETION_ENABLED", "\n".join(job(path, name)))
        publish = steps(job(MANUAL, "publish-status"))["Publish Claude Review result"]
        self.assertEqual(scalar(publish, "MANUAL_COMPLETION_ENABLED", 10), '"true"')

    def test_check_text_carries_no_agent_invocation_and_one_external_id(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                labels = re.findall(r"TRIGGER_LABEL: (.+)", text)
                self.assertEqual(len(labels), 3)
                self.assertEqual(len(set(labels)), 1)
                self.assertNotIn("@", labels[0])
                if path == AUTOMATIC:
                    for action, expected in (("ready_for_review", "Draft marked ready"),
                                             ("opened", "PR opened for review")):
                        github = pull_request_event(draft=False)
                        github["event"]["action"] = action
                        self.assertEqual(evaluate(labels[0], github), expected)
                else:
                    self.assertEqual(labels[0], "Manual request")
                self.assertEqual(len(set(re.findall(r"EXTERNAL_ID: (.+)", text))), 1)
                expected_mode = "automatic" if path == AUTOMATIC else "manual"
                self.assertEqual(re.findall(r"PR_REACTION_MODE: (.+)", text), [expected_mode] * 2)

    def test_claude_gets_only_the_review_built_ins_and_no_background_tasks(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                claude = steps(job(path, "review"))["Review pull request"]
                args = folded(claude, "claude_args", 10)
                self.assertIn('--tools "Read,Glob,Grep"', args)
                self.assertIn(
                    '--allowedTools "Read,Glob,Grep,mcp__github_inline_comment__create_inline_comment"', args
                )
                self.assertIn('--disallowedTools "Bash"', args)
                self.assertIn('          CLAUDE_CODE_DISABLE_BACKGROUND_TASKS: "1"', block(claude, "env", 8))

    def test_completion_is_verified_by_trusted_inline_code_right_after_claude(self):
        scripts = set()
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                named = steps(job(path, "review"))
                self.assertEqual(list(named)[-2:], ["Review pull request", VERIFY_STEP])
                verify = named[VERIFY_STEP]
                text = "\n".join(verify)
                self.assertEqual(scalar(verify, "id", 8), "verify")
                self.assertIsNone(scalar(verify, "uses", 8))
                self.assertIsNone(scalar(verify, "if", 8))
                self.assertEqual(
                    scalar(verify, "EXECUTION_FILE", 10), "${{ steps.review.outputs.execution_file }}"
                )
                self.assertEqual(scalar(verify, "REVIEW_HEAD_SHA", 10),
                                 "${{ github.event.pull_request.head.sha }}" if path == AUTOMATIC
                                 else "${{ needs.start-check.outputs.head_sha }}")
                metadata = "pr.json" if path == AUTOMATIC else "request.json"
                self.assertEqual(scalar(verify, "REVIEW_METADATA_FILE", 10),
                                 "${{ runner.temp }}/pr-review/" + metadata)
                self.assertEqual(scalar(verify, "REVIEW_DIFF_FILE", 10),
                                 "${{ runner.temp }}/pr-review/diff.patch")
                for untrusted in ("scripts/", "claude_review_check", "GITHUB_WORKSPACE", "./", "source "):
                    self.assertNotIn(untrusted, text)
                allowed = json.loads(scalar(verify, "ALLOWED_TOOLS", 10).strip("'"))
                args = folded(named["Review pull request"], "claude_args", 10)
                self.assertIn(f'--allowedTools "{",".join(allowed)}"', args)
                scripts.add(run_block(verify))
        self.assertEqual(len(scripts), 1)

    def test_only_verdict_and_bounded_receipts_flow_from_review_to_publication(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                outputs = block(job(path, "review"), "outputs", 4)
                self.assertIn("      completion_verified: ${{ steps.verify.outputs.completion_verified }}", outputs)
                self.assertIn("      completion_reason: ${{ steps.verify.outputs.completion_reason }}", outputs)
                self.assertIn("      finding_publication: ${{ steps.verify.outputs.finding_publication }}", outputs)
                self.assertEqual(len([line for line in outputs if line.strip()]), 4)
                publish = steps(job(path, "publish-status"))["Publish Claude Review result"]
                self.assertEqual(
                    scalar(publish, "COMPLETION_VERIFIED", 10), "${{ needs.review.outputs.completion_verified }}"
                )
                self.assertEqual(
                    scalar(publish, "COMPLETION_REASON", 10), "${{ needs.review.outputs.completion_reason }}"
                )
                self.assertEqual(scalar(publish, "FINDING_PUBLICATION", 10),
                                 "${{ needs.review.outputs.finding_publication }}")
                self.assertEqual(scalar(steps(job(path, "review"))["Review pull request"],
                                        "classify_inline_comments", 10), '"false"')
                self.assertNotIn("execution_file", "\n".join(job(path, "publish-status")))

    def test_review_job_uses_the_captured_snapshot_instead_of_live_pr_state(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                review = "\n".join(job(path, "review"))
                self.assertNotIn("gh pr view", review)
                self.assertNotIn("gh api", review)
                self.assertIn("name: pr-review-snapshot", review)

    def test_status_publication_is_only_in_trusted_jobs(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                start = "\n".join(job(path, "start-check"))
                publish = "\n".join(job(path, "publish-status"))
                review = "\n".join(job(path, "review"))
                self.assertIn("status_comment_id: ${{ steps.check.outputs.status_comment_id }}", start)
                self.assertIn('STATUS_COMMENTS_ENABLED: "true"', start)
                self.assertIn('STATUS_COMMENTS_ENABLED: "true"', publish)
                self.assertIn("BASE_REF:", start)
                self.assertIn("BASE_REF:", publish)
                self.assertIn("STATUS_COMMENT_ID: ${{ needs.start-check.outputs.status_comment_id }}", publish)
                for permission in ("issues: write", "pull-requests: write", "checks: write"):
                    self.assertNotIn(permission, review)
                self.assertNotIn("STATUS_COMMENTS_ENABLED", review)

    def test_trusted_helper_is_packaged_at_the_running_workflow_revision(self):
        for path in (AUTOMATIC, MANUAL):
            for name in ("start-check", "publish-status"):
                with self.subTest(workflow=path.name, job=name):
                    named = steps(job(path, name))
                    readiness = named["Check review tooling"]
                    self.assertEqual(scalar(readiness, "uses", 8), "$/actions/review-helper")
                    self.assertEqual(scalar(readiness, "command", 10), "create" if name == "start-check" else "finalize")
                    self.assertNotIn("actions/checkout", "\n".join(job(path, name)))
                    self.assertNotIn("ref:", "\n".join(readiness))
                    self.assertIn("REVIEW_HELPER: ${{ steps.tooling-ready.outputs.helper-path }}", "\n".join(job(path, name)))
        fallback = steps(job(MANUAL, "publish-status"))["Record failure without review tooling"]
        self.assertEqual(scalar(fallback, "if", 8), "always() && steps.tooling-ready.outcome != 'success'")
        self.assertIs(Evaluator("steps.tooling-ready.outcome != 'success'",
                               {"steps": {"tooling-ready": {"outcome": "skipped"}}}).evaluate(), True)
        self.assertEqual(scalar(steps(job(MANUAL, "publish-status"))["Publish Claude Review result"], "if", 8),
                         "steps.tooling-ready.outcome == 'success'")

    def test_stale_workflow_only_refreshes_existing_status_in_shared_queue(self):
        text = STALE.read_text()
        guard = folded(job(STALE, "refresh-status"), "if", 4)
        for event_name, action, changes, expected in (
            ("pull_request", "synchronize", {}, True),
            ("pull_request", "edited", {"base": {"ref": {"from": "main"}}}, True),
            ("pull_request", "edited", {"title": {"from": "old title"}}, False),
            ("pull_request", "ready_for_review", {}, False),
            ("push", "synchronize", {}, False),
        ):
            with self.subTest(event_name=event_name, action=action, changes=changes):
                event = pull_request_event()
                event["event_name"] = event_name
                event["event"].update(action=action, changes=changes)
                self.assertIs(evaluate(guard, event), expected)
        eligible = pull_request_event()
        eligible["event"]["action"] = "synchronize"
        self.assertEqual(evaluate(concurrency_group(STALE), eligible), f"claude-review-state-{REPO}-7")
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("queue: max", text)
        permissions = block(lines_of(STALE), "permissions", 0)
        self.assertEqual([line.strip() for line in permissions if line.strip()],
                         ["contents: read", "pull-requests: write", "issues: write"])
        self.assertNotIn("permissions:", "\n".join(job(STALE, "refresh-status")))
        for forbidden in ("checks: write", "id-token: write", "anthropics/", "CLAUDE_CODE_OAUTH_TOKEN", '"$REVIEW_HELPER" create', "actions/checkout"):
            self.assertNotIn(forbidden, text)
        self.assertIn("HEAD_SHA: ${{ github.event.pull_request.head.sha }}", text)
        self.assertIn("BASE_REF: ${{ github.event.pull_request.base.ref }}", text)
        self.assertIn("PR_REACTION_MODE: stale", text)
        self.assertIn('"$REVIEW_HELPER" stale', text)
        self.assertEqual(scalar(steps(job(STALE, "refresh-status"))["Check review tooling"], "uses", 8), "$/actions/review-helper")
        timeout = int(scalar(job(STALE, "refresh-status"), "timeout-minutes", 4)) * 60
        self.assertLessEqual(worst_case_seconds("stale") + JOB_OVERHEAD_SECONDS, timeout)

    def test_stale_workflow_uses_only_the_validated_bundled_helper(self):
        named = steps(job(STALE, "refresh-status"))
        readiness = named["Check review tooling"]
        self.assertEqual(scalar(readiness, "id", 8), "tooling-ready")
        self.assertEqual(scalar(readiness, "command", 10), "stale")
        command = named["Mark an existing older status stale"]
        self.assertEqual(scalar(command, "REVIEW_HELPER", 10), "${{ steps.tooling-ready.outputs.helper-path }}")
        self.assertEqual(scalar(command, "run", 8), 'python3 "$REVIEW_HELPER" stale')
        self.assertNotIn("base.sha", "\n".join(readiness))
        self.assertNotIn("continue-on-error:", "\n".join(readiness))



STUB_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    method = args[args.index("--method") + 1] if "--method" in args else "GET"
    body = json.loads(sys.stdin.read()) if "--input" in args else None
    log = os.environ["GH_STUB_LOG"]
    calls = [json.loads(line) for line in open(log)] if os.path.exists(log) else []
    with open(log, "a") as handle:
        handle.write(json.dumps({"method": method, "args": args, "body": body}) + "\\n")
    reaction_get = method == "GET" and any("/reactions" in arg for arg in args)
    key = "REACTIONS" if reaction_get else method
    if method == "GET" and not reaction_get:
        if any("/issues/comments/55" in arg for arg in args):
            key = "STATUS"
        elif any(arg.endswith("/pulls/7") for arg in args):
            key = "PR"
        elif any("/git/ref/heads/" in arg for arg in args):
            key = "REF"
        elif any("/comments/42" in arg for arg in args):
            key = "TRIGGER"
    responses = json.loads(os.environ.get("GH_STUB_RESPONSES", "{}")).get(
        key, [{"stdout": "[]"}] if reaction_get else [{"stdout": "{}"}]
    )
    seen = sum(
        1 for c in calls
        if c["method"] == method
        and (method != "GET" or (
            ("REACTIONS" if any("/reactions" in arg for arg in c["args"]) else
             "STATUS" if any("/issues/comments/55" in arg for arg in c["args"]) else
             "PR" if any(arg.endswith("/pulls/7") for arg in c["args"]) else
             "REF" if any("/git/ref/heads/" in arg for arg in c["args"]) else
             "TRIGGER" if any("/comments/42" in arg for arg in c["args"]) else "GET") == key))
    )
    response = responses[min(seen, len(responses) - 1)]
    if response.get("hang"):
        import time
        time.sleep(60)
    if response.get("fail"):
        sys.exit(1)
    sys.stdout.write(response["stdout"])
    """
)

# The runner image has coreutils timeout; this stands in where it is missing.
TIMEOUT_SHIM = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import subprocess, sys
    child = subprocess.Popen(sys.argv[2:])
    try:
        sys.exit(child.wait(timeout=float(sys.argv[1])))
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        sys.exit(124)
    """
)
FALLBACK_STEP = "Record failure without review tooling"
VERIFY_STEP = "Verify review completion"

HEAD = "a" * 40
BASE_REF = "main"
EXTERNAL_ID = "Claude Review/5/1"


def history(*runs):
    """Two concatenated pages, the way `gh api --paginate` prints them."""
    return json.dumps({"check_runs": [{"id": 70, "external_id": "Claude Review/4/1"}]}) + json.dumps(
        {"check_runs": list(runs)}
    )


def pr_identity(head=HEAD, base_ref=BASE_REF):
    return json.dumps({"head": {"sha": head}, "base": {
        "ref": base_ref, "sha": "d" * 40, "repo": {"full_name": REPO}}})


class ToolingFallbackScriptTests(unittest.TestCase):
    """Runs the fallback step's actual shell against a stub gh."""

    def run_fallback(self, path=AUTOMATIC, responses=None, **overrides):
        script = run_block(steps(job(path, "publish-status"))["Record failure without review tooling"])
        from test_claude_review_check import presentation_owner
        owner = overrides.pop("OWNER_FIXTURE", None) or presentation_owner(repo=REPO, base_ref=overrides.get("BASE_REF", BASE_REF))
        owner_json = json.dumps(owner)
        owner_marker = check.owner_marker(owner)
        responses = dict(responses or {})
        generic = responses.get("GET", [])
        statuses, prs = [], []
        for response in generic:
            try:
                document = json.loads(response.get("stdout", ""))
            except ValueError:
                continue
            if isinstance(document, dict) and "head" in document:
                prs.append(response)
            elif isinstance(document, dict) and "user" in document:
                document["body"] += "\n" + owner_marker
                statuses.append({"stdout": json.dumps(document)})
        responses.setdefault("PR", prs or generic[-1:] or [{"stdout": pr_identity()}])
        responses.setdefault("REF", [{"stdout": json.dumps({"ref": f"refs/heads/{owner['base_ref']}",
            "object": {"type": "commit", "sha": "d" * 40}})}])
        default_status = {"id": 55, "user": {"login": "github-actions[bot]", "type": "Bot"},
                          "body": check.status_comment_body(HEAD, owner["base_ref"], "in_progress", owner=owner)}
        responses.setdefault("STATUS", statuses or [{"stdout": json.dumps(default_status)}])
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "gh"
            stub.write_text(STUB_GH)
            stub.chmod(0o755)
            if shutil.which("timeout") is None:
                shim = Path(tmp) / "timeout"
                shim.write_text(TIMEOUT_SHIM)
                shim.chmod(0o755)
            log = Path(tmp) / "gh.log"
            environment = {
                "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
                "GH_STUB_LOG": str(log),
                "GH_STUB_RESPONSES": json.dumps(responses),
                "CLAUDE_REVIEW_RETRY_DELAY": "0",
                "PRESENTATION_OWNER": owner_json,
                "GITHUB_RUN_ID": "5", "GITHUB_RUN_ATTEMPT": "1",
                "CLAUDE_REVIEW_GH_TIMEOUT": "1",
                "REPO": REPO,
                "BASE_SHA": "d" * 40,
                "MERGE_BASE_SHA": "c" * 40,
                "RUNTIME_REPOSITORY": "example/review-runtime",
                "RUNTIME_SHA": "f" * 40,
                "RUNTIME_WORKFLOW_PATH": ".github/workflows/claude-review.yml",
                "HEAD_SHA": HEAD,
                "BASE_REF": BASE_REF,
                "IS_FORK": "false",
                "CHECK_RUN_ID": "99",
                "EXTERNAL_ID": EXTERNAL_ID,
                "DETAILS_URL": "https://example.invalid/run",
                **overrides,
            }
            result = subprocess.run(
                ["bash", "-eo", "pipefail", "-c", script], env=environment, capture_output=True, text=True
            )
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    def methods(self, calls):
        return [call["method"] for call in calls
                if not (call["method"] == "GET" and any("/reactions" in arg for arg in call["args"]))]

    def test_unavailable_bundled_helper_routes_to_failure_publication(self):
        for path in (AUTOMATIC, MANUAL):
            for name in ("start-check", "publish-status"):
                with self.subTest(workflow=path.name, job=name):
                    readiness = steps(job(path, name))["Check review tooling"]
                    self.assertEqual(scalar(readiness, "uses", 8), "$/actions/review-helper")
                    # Actual preparation-script execution covers missing/invalid
                    # bundles below; the outcome must select trusted fallback.
                    if name == "publish-status":
                        publish = scalar(steps(job(path, name))["Publish Claude Review result"], "if", 8)
                        fallback = scalar(steps(job(path, name))[FALLBACK_STEP], "if", 8)
                        context = {"steps": {"tooling-ready": {"outcome": "failure"}}}
                        self.assertIs(Evaluator(publish, context).evaluate(), False)
                        self.assertIs(Evaluator(fallback.removeprefix("always() && "), context).evaluate(), True)
                        result, calls = self.run_fallback(path, STATUS_COMMENT_ID="")
                        self.assertEqual(result.returncode, 1)
                        self.assertEqual(calls[0]["body"]["conclusion"], "failure")
                    else:
                        create = steps(job(path, name))["Start Claude Review check"]
                        self.assertNotIn("pull_request.head", "\n".join(readiness))
                        self.assertNotIn("github.sha", "\n".join(readiness))
                        publish_gate = scalar(job(path, "publish-status"), "if", 4)
                        self.assertIs(Evaluator(publish_gate.removeprefix("always() && "),
                                                {"needs": {"start-check": {"result": "failure"}}}).evaluate(), True)
                        if path == MANUAL:
                            self.assertIs(Evaluator(scalar(create, "if", 8),
                                                    {"steps": {"tooling-ready": {"outcome": "failure"}}}).evaluate(), False)

    def test_completes_the_known_check_as_failure_and_fails_the_step(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(path)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.methods(calls), ["PATCH"])
                self.assertIn(f"repos/{REPO}/check-runs/99", calls[0]["args"])
                self.assertEqual((calls[0]["body"]["status"], calls[0]["body"]["conclusion"]), ("completed", "failure"))

    def test_fallback_failure_evidence_retains_the_runtime_and_diff_identity(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(path, PR_NUMBER="7")
                self.assertEqual(result.returncode, 1, result.stderr)
                text = calls[0]["body"]["output"]["text"]
                evidence = json.loads(text.removeprefix("```json\n").removesuffix("\n```"))
                self.assertEqual(evidence, {
                    "reviewed_sha": HEAD, "pr_number": 7,
                    "base_sha": "d" * 40, "merge_base_sha": "c" * 40,
                    "runtime_repository": "example/review-runtime",
                    "runtime_sha": "f" * 40,
                    "runtime_workflow_path": ".github/workflows/claude-review.yml",
                })

    def test_known_head_fallback_publishes_failure_without_a_pr_number(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(path)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.methods(calls), ["PATCH"])
                body = calls[0]["body"]
                self.assertEqual(body["conclusion"], "failure")
                text = body["output"]["text"]
                evidence = json.loads(text.removeprefix("```json\n").removesuffix("\n```"))
                self.assertIsNone(evidence["pr_number"])
                self.assertEqual(evidence["reviewed_sha"], HEAD)
                self.assertEqual(evidence["runtime_sha"], "f" * 40)

    def test_unreported_run_found_by_external_id_is_patched_without_posting(self):
        found = {"GET": [{"stdout": history({"id": 77, "external_id": EXTERNAL_ID, "status": "in_progress"})}]}
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(path, found, CHECK_RUN_ID="")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.methods(calls), ["GET", "PATCH"])
                self.assertIn("filter=all", calls[0]["args"][-1])
                self.assertIn("--paginate", calls[0]["args"])
                self.assertIn(f"repos/{REPO}/check-runs/77", calls[1]["args"])
                self.assertEqual(calls[1]["body"]["conclusion"], "failure")

    def test_failed_lookup_is_retried_rather_than_followed_by_a_post(self):
        responses = {
            "GET": [{"fail": True}, {"stdout": history({"id": 77, "external_id": EXTERNAL_ID})}],
        }
        result, calls = self.run_fallback(MANUAL, responses, CHECK_RUN_ID="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["GET", "GET", "PATCH"])
        self.assertIn(f"repos/{REPO}/check-runs/77", calls[2]["args"])

    def test_unresolved_lookup_publishes_nothing(self):
        result, calls = self.run_fallback(AUTOMATIC, {"GET": [{"fail": True}]}, CHECK_RUN_ID="")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(call["method"] == "PATCH" for call in calls))

    def test_confirmed_absence_allows_one_completed_failure_post(self):
        result, calls = self.run_fallback(AUTOMATIC, {"GET": [{"stdout": history()}]}, CHECK_RUN_ID="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["GET", "POST"])
        body = calls[1]["body"]
        self.assertEqual(
            (body["name"], body["head_sha"], body["status"], body["conclusion"], body["external_id"]),
            ("Claude Review", HEAD, "completed", "failure", EXTERNAL_ID),
        )

    def test_manual_fork_without_check_repairs_known_status_before_failing(self):
        owned = {"user": {"login": "github-actions[bot]", "type": "Bot"},
                 "body": "<!-- claude-review-runtime:claude-review-status -->"}
        result, calls = self.run_fallback(
            MANUAL,
            {"GET": [{"stdout": history()}, {"stdout": pr_identity()},
                     {"stdout": json.dumps(owned)}]},
            CHECK_RUN_ID="", IS_FORK="true", STATUS_COMMENT_ID="55", PR_NUMBER="7",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls)[-1], "PATCH")
        body = next(call["body"]["body"] for call in calls if call["method"] == "PATCH")
        self.assertIn("Review completion cannot be established", body)
        self.assertIn("Check publication is unavailable", body)
        self.assertIn("claude-review-status-base-ref:main", body)

    def test_manual_fork_no_check_repair_skips_changed_or_unverifiable_identity(self):
        for response in ({"stdout": pr_identity("b" * 40)},
                         {"stdout": pr_identity(HEAD, "release")}, {"fail": True}):
            with self.subTest(response=response):
                result, calls = self.run_fallback(
                    MANUAL, {"GET": [{"stdout": history()}, response]},
                    CHECK_RUN_ID="", IS_FORK="true", STATUS_COMMENT_ID="55", PR_NUMBER="7",
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.methods(calls)[-1], "GET")

    def test_manual_fork_no_check_repair_leaves_unowned_comment_untouched(self):
        unowned = {"user": {"login": "other", "type": "User"},
                   "body": "<!-- claude-review-runtime:claude-review-status -->"}
        result, calls = self.run_fallback(
            MANUAL, {"GET": [{"stdout": history()}, {"stdout": pr_identity()},
                             {"stdout": json.dumps(unowned)}]},
            CHECK_RUN_ID="", IS_FORK="true", STATUS_COMMENT_ID="55", PR_NUMBER="7",
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(call["method"] == "PATCH" for call in calls))

    def test_manual_fork_no_check_repair_failure_does_not_change_exit(self):
        owned = {"user": {"login": "github-actions[bot]", "type": "Bot"},
                 "body": "<!-- claude-review-runtime:claude-review-status -->"}
        result, calls = self.run_fallback(
            MANUAL,
            {"GET": [{"stdout": history()}, {"stdout": pr_identity()},
                     {"stdout": json.dumps(owned)}], "PATCH": [{"fail": True}]},
            CHECK_RUN_ID="", IS_FORK="true", STATUS_COMMENT_ID="55", PR_NUMBER="7",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls)[-1], "PATCH")

    def test_retries_a_failing_write_a_bounded_number_of_times(self):
        result, calls = self.run_fallback(responses={"PATCH": [{"fail": True}]})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["PATCH"] * 3)

    def test_no_exact_head_sha_means_no_publication(self):
        for overrides in ({"HEAD_SHA": "", "CHECK_RUN_ID": ""}, {"HEAD_SHA": ""}):
            with self.subTest(overrides=overrides):
                result, calls = self.run_fallback(MANUAL, **overrides)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(calls, [])

    def test_a_hanging_lookup_is_cut_off_and_retried_before_patching_the_found_run(self):
        responses = {"GET": [{"hang": True}, {"stdout": history({"id": 77, "external_id": EXTERNAL_ID})}]}
        result, calls = self.run_fallback(MANUAL, responses, CHECK_RUN_ID="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["GET", "GET", "PATCH"])
        self.assertIn(f"repos/{REPO}/check-runs/77", calls[2]["args"])

    def test_a_lookup_that_always_hangs_publishes_nothing(self):
        result, calls = self.run_fallback(AUTOMATIC, {"GET": [{"hang": True}]}, CHECK_RUN_ID="")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["GET"] * 3)

    def test_a_hanging_write_is_cut_off_and_retried_a_bounded_number_of_times(self):
        result, calls = self.run_fallback(AUTOMATIC, {"PATCH": [{"hang": True}, {"stdout": "{}"}]})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["PATCH", "PATCH"])
        self.assertIn("recorded failure", result.stdout)

    def test_every_fallback_api_call_is_individually_bounded(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                script = run_block(steps(job(path, "publish-status"))[FALLBACK_STEP])
                code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
                self.assertEqual(code.count("gh api"), 3)
                self.assertEqual(code.count('timeout "$attempt_timeout" gh api'), 2)
                self.assertIn('timeout "$remaining" gh api "$@"', code)
                self.assertIn("presentation_deadline=$((SECONDS + 120))", code)

    def test_known_workflow_owned_comment_is_repaired_after_check_fallback(self):
        owned = {"user": {"login": "github-actions[bot]", "type": "Bot"},
                 "body": "<!-- claude-review-runtime:claude-review-status -->"}
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                label = "Draft marked ready" if path == AUTOMATIC else "Manual request"
                result, calls = self.run_fallback(
                    path, {"GET": [{"stdout": pr_identity()}, {"stdout": json.dumps(owned)}]},
                    STATUS_COMMENT_ID="55", PR_NUMBER="7", TRIGGER_LABEL=label,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.methods(calls)[-1], "PATCH")
                repaired = next(call for call in calls if call["method"] == "PATCH" and any("issues/comments/55" in arg for arg in call["args"]))
                self.assertIn("❌ Claude Review incomplete", repaired["body"]["body"])
                self.assertIn(HEAD, repaired["body"]["body"])
                self.assertIn(f"**Current commit:** `{HEAD[:7]}`\n**Trigger:** {label}\n", repaired["body"]["body"])
                self.assertNotIn("Authority:", repaired["body"]["body"])
                self.assertNotIn("Check", repaired["body"]["body"])

    def test_fallback_records_an_encoded_base_ref(self):
        owned = {"user": {"login": "github-actions[bot]", "type": "Bot"},
                 "body": "<!-- claude-review-runtime:claude-review-status -->"}
        result, calls = self.run_fallback(
            MANUAL,
            {"GET": [{"stdout": pr_identity(base_ref="release/2026")},
                     {"stdout": json.dumps(owned)}]},
            BASE_REF="release/2026", STATUS_COMMENT_ID="55", PR_NUMBER="7",
        )
        self.assertEqual(result.returncode, 1)
        repaired = next(call for call in calls if call["method"] == "PATCH" and any("issues/comments/55" in arg for arg in call["args"]))
        self.assertIn("base-ref:release%2F2026", repaired["body"]["body"])
        self.assertTrue(any("repos/example/repository/git/ref/heads/release%2F2026" in call["args"]
                            for call in calls))

    def test_invalid_direct_ref_never_repairs_shared_presentation(self):
        invalid = [{"fail": True}, {"stdout": "{}"}, {"stdout": "[]"},
            {"stdout": json.dumps({"ref": "refs/heads/wrong", "object": {"type": "commit", "sha": "d" * 40}})},
            {"stdout": json.dumps({"ref": "refs/heads/main", "object": {"type": "tag", "sha": "d" * 40}})},
            {"stdout": json.dumps({"ref": "refs/heads/main", "object": {"type": "commit", "sha": 5}})},
            {"stdout": '{"ref":"refs/heads/main","ref":"refs/heads/main",'
                       '"object":{"type":"commit","sha":"' + "d" * 40 + '"}}'}]
        for path in (AUTOMATIC, MANUAL):
            for response in invalid:
                with self.subTest(workflow=path.name, response=response):
                    result, calls = self.run_fallback(path, {"REF": [response]},
                                                     PR_NUMBER="7", STATUS_COMMENT_ID="55")
                    self.assertEqual(result.returncode, 1)
                    writes = [c for c in calls if c["method"] in ("PATCH", "POST", "DELETE")]
                    self.assertEqual(len(writes), 1)
                    self.assertIn(f"repos/{REPO}/check-runs/99", writes[0]["args"])

    def test_retarget_during_ref_lookup_cannot_repair_shared_presentation(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(path,
                    {"PR": [{"stdout": pr_identity()}, {"stdout": pr_identity(base_ref="release")}]},
                    PR_NUMBER="7", STATUS_COMMENT_ID="55")
                self.assertEqual(result.returncode, 1)
                self.assertEqual([c["method"] for c in calls if c["method"] != "GET"], ["PATCH"])

    def test_projection_ahead_of_direct_tip_cannot_claim_current_repair(self):
        result, calls = self.run_fallback(MANUAL,
            {"PR": [{"stdout": pr_identity().replace("d" * 40, "b" * 40)}]},
            PR_NUMBER="7", STATUS_COMMENT_ID="55")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c["method"] for c in calls if c["method"] != "GET"], ["PATCH"])

    def test_fallback_preserves_last_review_and_clears_only_workflow_reactions(self):
        previous = check.status_comment_body("b" * 40, "main", "success")
        owned = {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": previous}
        reactions = json.dumps([
            {"id": 70, "content": "+1", "user": {"login": "github-actions[bot]", "type": "User"}},
            {"id": 74, "content": "eyes", "user": {"login": "github-actions[bot]", "type": "User"}},
            {"id": 71, "content": "eyes", "user": {"login": "claude[bot]", "type": "Bot"}},
            {"id": 72, "content": "+1", "user": {"login": "example-user", "type": "User"}},
            {"id": 73, "content": "+1", "user": {"login": "chatgpt-codex-connector[bot]", "type": "User"}},
        ])
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                result, calls = self.run_fallback(
                    path, {"GET": [{"stdout": pr_identity()}, {"stdout": json.dumps(owned)}],
                           "REACTIONS": [{"stdout": reactions}]},
                    STATUS_COMMENT_ID="55", PR_NUMBER="7",
                )
                self.assertEqual(result.returncode, 1)
                repaired = next(call for call in calls if call["method"] == "PATCH" and any("issues/comments/55" in arg for arg in call["args"]))
                self.assertIn("claude-review-last-reviewed-head:" + "b" * 40, repaired["body"]["body"])
                self.assertIn("**Current commit:** `aaaaaaa`", repaired["body"]["body"])
                deleted = [call for call in calls if call["method"] == "DELETE"]
                self.assertEqual(len(deleted), 2)
                self.assertIn(f"repos/{REPO}/issues/7/reactions/70", deleted[0]["args"])
                self.assertIn(f"repos/{REPO}/issues/7/reactions/74", deleted[1]["args"])
                self.assertFalse(any("issues/comments/55/reactions" in arg
                                     for call in calls for arg in call["args"]))

    def test_fallback_without_known_status_owner_does_not_clear_reactions(self):
        reaction = {"id": 70, "content": "+1",
                    "user": {"login": "github-actions[bot]", "type": "User"}}
        result, calls = self.run_fallback(
            responses={"GET": [{"stdout": pr_identity()}],
                       "REACTIONS": [{"stdout": json.dumps([reaction])}]},
            PR_NUMBER="7", STATUS_COMMENT_ID="",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["PATCH"])
        self.assertFalse(any(call["method"] == "DELETE" for call in calls))

    def test_fallback_preserves_legacy_review_history_with_verified_owner(self):
        old_sha = "b" * 40
        legacy = (f"{check.STATUS_MARKER}\n{check.STATUS_HEAD_PREFIX}{old_sha} -->\n"
                  f"{check.STATUS_BASE_REF_PREFIX}main -->\n"
                  "Claude Review completed with no findings for this commit.")
        owned = {"user": {"login": "github-actions[bot]", "type": "Bot"}, "body": legacy}
        result, calls = self.run_fallback(
            MANUAL,
            {"GET": [{"stdout": pr_identity()}, {"stdout": json.dumps(owned)}]},
            STATUS_COMMENT_ID="55", PR_NUMBER="7",
        )
        self.assertEqual(result.returncode, 1)
        repaired = next(call for call in calls if call["method"] == "PATCH" and any("issues/comments/55" in arg for arg in call["args"]))
        self.assertIn(f"claude-review-last-reviewed-head:{old_sha}", repaired["body"]["body"])
        self.assertIn("claude-review-last-reviewed-result:success", repaired["body"]["body"])

    def test_failed_or_unowned_comment_repair_keeps_check_fallback(self):
        unowned = {"user": {"login": "example-user", "type": "User"},
                   "body": "<!-- claude-review-runtime:claude-review-status -->"}
        result, calls = self.run_fallback(
            responses={"GET": [{"stdout": pr_identity()}, {"stdout": json.dumps(unowned)}]},
            STATUS_COMMENT_ID="55", PR_NUMBER="7",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["PATCH", "GET"])
        self.assertEqual(calls[0]["body"]["conclusion"], "failure")

    def test_fallback_never_rewrites_a_newer_or_unverifiable_status(self):
        for response in ({"stdout": pr_identity("b" * 40)},
                         {"stdout": pr_identity(HEAD, "release")}, {"fail": True}):
            for path in (AUTOMATIC, MANUAL):
                with self.subTest(workflow=path.name, response=response):
                    result, calls = self.run_fallback(
                        path, {"GET": [response]}, STATUS_COMMENT_ID="55", PR_NUMBER="7"
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(self.methods(calls)[0], "PATCH")
                    self.assertEqual(len([call for call in calls if call["method"] == "PATCH"]), 1)
                    self.assertEqual(calls[0]["body"]["conclusion"], "failure")

    def test_unpublishable_check_repairs_known_comment_as_incomplete(self):
        owned = {"user": {"login": "github-actions[bot]", "type": "Bot"},
                 "body": "<!-- claude-review-runtime:claude-review-status -->"}
        result, calls = self.run_fallback(
            responses={
                "PATCH": [{"fail": True}] * 3 + [{"stdout": "{}"}],
                "GET": [{"stdout": pr_identity()}, {"stdout": json.dumps(owned)}],
            },
            STATUS_COMMENT_ID="55", PR_NUMBER="7", TRIGGER_LABEL="PR opened for review",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls)[-1], "PATCH")
        repaired = next(call for call in calls if call["method"] == "PATCH" and any("issues/comments/55" in arg for arg in call["args"]))
        self.assertIn("could not be published", repaired["body"]["body"])
        self.assertNotIn("Authority:", repaired["body"]["body"])
        self.assertIn("authoritative Check result could not be published", repaired["body"]["body"])
        self.assertIn("**Trigger:** PR opened for review", repaired["body"]["body"])

    def test_fallback_worst_case_fits_its_step_timeout(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                step = steps(job(path, "publish-status"))[FALLBACK_STEP]
                script = run_block(step)
                attempt = int(re.search(r"attempt_timeout=\$\{CLAUDE_REVIEW_GH_TIMEOUT:-(\d+)\}", script).group(1))
                delay = int(re.search(r"delay=\$\{CLAUDE_REVIEW_RETRY_DELAY:-(\d+)\}", script).group(1))
                attempts = [int(n) for n in re.findall(r"for attempt in ([\d ]+); do", script)[0].split()]
                self.assertEqual(len(re.findall(r"for attempt in 1 2 3; do", script)), 2)
                per_phase = len(attempts) * attempt + sum(n * delay for n in attempts[:-1])
                step_seconds = int(scalar(step, "timeout-minutes", 8)) * 60
                # Lookup and write phases, then known-comment repair and
                # best-effort removal of two obsolete reactions.
                self.assertLessEqual(2 * per_phase + check.PRESENTATION_SECONDS, step_seconds)

    def test_fork_without_a_matching_run_is_not_given_a_new_one(self):
        result, calls = self.run_fallback(MANUAL, {"GET": [{"stdout": history()}]}, CHECK_RUN_ID="", IS_FORK="true")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.methods(calls), ["GET"])

# --- Execution-stream fixtures for the completion verifier.

REVIEW_TOOLS = ["Read", "Glob", "Grep", "mcp__github_inline_comment__create_inline_comment"]


def init_message(tools=REVIEW_TOOLS):
    return {"type": "system", "subtype": "init", "tools": list(tools)}


def tool_use(use_id, name, parent=None, **inputs):
    content = [{"type": "tool_use", "id": use_id, "name": name, "input": inputs}]
    return {"type": "assistant", "parent_tool_use_id": parent, "message": {"content": content}}


def tool_result(use_id, parent=None, **flags):
    content = [{"type": "tool_result", "tool_use_id": use_id, "content": "ok", **flags}]
    return {"type": "user", "parent_tool_use_id": parent, "message": {"content": content}}


def final_text(text="No significant issues found."):
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [{"type": "text", "text": text}]}}


def result(subtype="success", is_error=False):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": 4}


METADATA_CONTENT = '{"head_sha": "' + HEAD + '",\n"number": 7}\n'
DIFF_CONTENT = "diff --git a/file.py b/file.py\n+changed line\n"


def read_result(use_id, source, start=1, stop=None, *, blocks=False, **flags):
    # Read text sent to the model is numbered; structured SDK sidecars alone
    # do not establish what was presented. Preserve content, including spaces.
    lines = source.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    rendered = "\n".join(f"{index:6d}→{line}"
                         for index, line in enumerate(lines, 1)
                         if index >= start and (stop is None or index <= stop))
    content = [{"type": "text", "text": rendered}] if blocks else rendered
    return tool_result(use_id, content=content, **flags)


def inline_result(use_id="t3", comment_id=11, **flags):
    return tool_result(use_id, content=[{"type": "text", "text": json.dumps({
        "success": True, "comment_id": comment_id,
        "html_url": f"https://github.com/owner/repo/pull/7#discussion_r{comment_id}",
        "path": "file.py", "line": 1,
    })}], **flags)


def clean_stream():
    return [
        init_message(),
        tool_use("t1", "Read", file_path="/captured/metadata.json"),
        read_result("t1", METADATA_CONTENT),
        tool_use("t2", "Read", file_path="/captured/diff.patch"),
        read_result("t2", DIFF_CONTENT),
        tool_use("t3", "mcp__github_inline_comment__create_inline_comment", commit_id=HEAD),
        inline_result(),
        final_text(),
        result(),
    ]


class CompletionVerifierTests(unittest.TestCase):
    """Runs the verifier step's actual shell against execution-stream fixtures."""

    def verify(self, stream=None, *, raw=None, path=AUTOMATIC, execution_file=None,
               metadata=METADATA_CONTENT, diff=DIFF_CONTENT):
        step = steps(job(path, "review"))[VERIFY_STEP]
        script = run_block(step)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "github_output"
            record = Path(tmp) / "claude-execution-output.json"
            metadata_file, diff_file = Path(tmp) / "metadata.json", Path(tmp) / "diff.patch"
            metadata_file.write_text(metadata)
            if isinstance(diff, bytes):
                diff_file.write_bytes(diff)
            else:
                diff_file.write_text(diff)
            if raw is not None:
                record.write_text(raw)
            elif stream is not None:
                record.write_text(json.dumps(stream).replace("/captured/metadata.json", str(metadata_file))
                                  .replace("/captured/diff.patch", str(diff_file)))
            environment = {
                "PATH": os.environ["PATH"],
                "GITHUB_OUTPUT": str(output),
                "EXECUTION_FILE": str(record) if execution_file is None else execution_file,
                "ALLOWED_TOOLS": scalar(step, "ALLOWED_TOOLS", 10).strip("'"),
                "REVIEW_HEAD_SHA": HEAD,
                "REVIEW_METADATA_FILE": str(metadata_file),
                "REVIEW_DIFF_FILE": str(diff_file),
            }
            completed = subprocess.run(
                ["bash", "-eo", "pipefail", "-c", script], env=environment, capture_output=True, text=True
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.last_stdout = completed.stdout
        self.last_outputs = values
        return values["completion_verified"], values["completion_reason"]

    def assertRejected(self, stream, reason, **kwargs):
        self.assertEqual(self.verify(stream, **kwargs), ("false", reason))

    def test_a_normal_foreground_review_is_verified_in_both_workflows(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                self.assertEqual(self.verify(clean_stream(), path=path), ("true", "verified"))

    def test_both_captured_inputs_require_complete_successful_read_content(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                self.assertRejected([init_message(), final_text(), result()],
                                    "captured_inputs_not_read", path=path)
                for use_index, response_index, source in ((1, 2, METADATA_CONTENT), (3, 4, DIFF_CONTENT)):
                    stream = clean_stream()
                    stream[use_index]["message"]["content"][0]["input"]["file_path"] = "/unrelated/file"
                    self.assertRejected(stream, "captured_inputs_not_read", path=path)
                    stream = clean_stream()
                    stream[response_index]["message"]["content"][0]["is_error"] = True
                    self.assertRejected(stream, "captured_inputs_not_read", path=path)
                    stream = clean_stream()
                    del stream[use_index:response_index + 1]
                    self.assertRejected(stream, "captured_inputs_not_read", path=path)
                    for partial in (read_result("t1" if use_index == 1 else "t2", source, stop=1),
                                    read_result("t1" if use_index == 1 else "t2", source, start=2),
                                    tool_result("t1" if use_index == 1 else "t2", content="read successfully")):
                        stream = clean_stream()
                        stream[response_index] = partial
                        self.assertRejected(stream, "captured_inputs_not_read", path=path)

    def test_oversized_diff_needs_actual_complete_coverage_in_both_paths(self):
        diff = "\n".join(f"+line {i}" for i in range(2101)) + "\n"
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                stream = clean_stream()
                stream[4] = read_result("t2", diff, stop=2000)
                stream[4]["tool_use_result"] = {"type": "text", "file": {
                    "totalLines": 2101, "numLines": 2101, "startLine": 1, "content": diff}}
                self.assertRejected(stream, "captured_inputs_not_read", path=path, diff=diff)
                # A second successful range presents the rest. Coverage comes
                # from content, not optimistic sidecar counts or requested limits.
                stream[7:7] = [tool_use("rest", "Read", file_path="/captured/diff.patch", offset=2001, limit=101),
                               read_result("rest", diff, start=2001, blocks=True)]
                self.assertEqual(self.verify(stream, path=path, diff=diff), ("true", "verified"))
                stream[8]["message"]["content"][0]["is_error"] = True
                self.assertRejected(stream, "captured_inputs_not_read", path=path, diff=diff)

    def test_supported_read_rendering_preserves_payload_and_empty_file_evidence(self):
        empty_notice = "<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>"
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                for separator in ("\t", ":", "→"):
                    diff = "+\tcolon: and arrow→ stay in content\r\n+second line"
                    stream = clean_stream()
                    stream[4] = read_result("t2", diff.replace("\r\n", "\n"))
                    content = stream[4]["message"]["content"][0]
                    content["content"] = "PARTIAL view banner\n" + content["content"].replace("1→", "1" + separator).replace("2→", "2" + separator) + "\n<system-reminder>Other text</system-reminder>"
                    self.assertEqual(self.verify(stream, path=path, diff=diff), ("true", "verified"))
                stream = clean_stream()
                stream[4] = tool_result("t2", content=empty_notice)
                self.assertEqual(self.verify(stream, path=path, diff=""), ("true", "verified"))
                stream[4] = tool_result("t2", content="read successfully")
                self.assertRejected(stream, "captured_inputs_not_read", path=path, diff="")

    def test_lossy_utf8_decoding_cannot_establish_complete_input_coverage(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                stream = clean_stream()
                stream[4] = read_result("t2", "+replacement \ufffd\n")
                self.assertRejected(stream, "unsupported_captured_input_encoding", path=path,
                                    diff=b"+replacement \xff\n")

    def test_truncated_or_changed_lines_do_not_count_as_coverage(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                diff = "+" + "x" * 3000 + "\n"
                stream = clean_stream()
                stream[4] = read_result("t2", diff[:2000] + "... [truncated]\n")
                self.assertRejected(stream, "captured_inputs_not_read", path=path, diff=diff)
                stream = clean_stream()
                stream[4] = read_result("t2", DIFF_CONTENT.replace("changed", "different"))
                self.assertRejected(stream, "captured_inputs_not_read", path=path)
                # Line numbers matter even when the returned text occurs elsewhere.
                stream[4] = read_result("t2", DIFF_CONTENT)
                stream[4]["message"]["content"][0]["content"] = stream[4]["message"]["content"][0]["content"].replace("2→", "1→")
                self.assertRejected(stream, "captured_inputs_not_read", path=path)
                stream[4] = read_result("t2", DIFF_CONTENT, blocks=True)
                self.assertEqual(self.verify(stream, path=path), ("true", "verified"))

    def test_every_inline_invocation_must_target_the_captured_head(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                for inputs in ({}, {"commit_id": "b" * 40}, {"commit_id": HEAD.upper()}, {"commit_id": None}):
                    stream = clean_stream()
                    stream[5]["message"]["content"][0]["input"] = inputs
                    self.assertRejected(stream, "wrong_inline_commit_identity", path=path)
                self.assertEqual(self.verify(clean_stream(), path=path), ("true", "verified"))
                stream = clean_stream()
                stream[7:7] = [tool_use("wrong", "mcp__github_inline_comment__create_inline_comment", commit_id="b" * 40),
                               tool_result("wrong")]
                self.assertRejected(stream, "wrong_inline_commit_identity", path=path)

    def test_failed_inline_publication_cannot_verify_a_clean_review(self):
        for path in (AUTOMATIC, MANUAL):
            with self.subTest(workflow=path.name):
                stream = clean_stream()
                stream[6] = tool_result("t3", is_error=True)
                self.assertRejected(stream, "errored_inline_tool_result", path=path)
                # A later successful retry does not erase the failed attempt.
                stream[7:7] = [tool_use("retry", "mcp__github_inline_comment__create_inline_comment", commit_id=HEAD),
                               tool_result("retry", is_error=False)]
                self.assertRejected(stream, "errored_inline_tool_result", path=path)
                stream = clean_stream()
                stream[6] = inline_result(is_error=False)
                self.assertEqual(self.verify(stream, path=path), ("true", "verified"))

    def test_missing_or_malformed_evidence_is_not_verified(self):
        self.assertRejected(None, "missing_execution_file")
        self.assertRejected(None, "missing_execution_file", execution_file="")
        self.assertRejected(None, "malformed_execution_file", raw="{not json")
        self.assertRejected(None, "malformed_execution_file", raw='{"type": "result"}')
        self.assertRejected(None, "malformed_execution_file", raw="[]")
        self.assertRejected(None, "malformed_execution_file", raw='["text"]')

    def test_the_run_must_end_on_one_clean_result(self):
        body = clean_stream()[:-1]
        self.assertRejected(body, "no_clean_terminal_result")
        self.assertRejected(body + [result("error_max_turns", True)], "no_clean_terminal_result")
        self.assertRejected(body + [result("error_during_execution", True)], "no_clean_terminal_result")
        self.assertRejected(body + [result(is_error=True)], "no_clean_terminal_result")
        self.assertRejected(body + [result(), result()], "no_clean_terminal_result")
        self.assertRejected(body + [result(), final_text()], "no_clean_terminal_result")

    def test_review_tools_must_be_present_and_agent_absent_while_unused_extras_are_tolerated(self):
        reason = "review_tools_not_as_configured"
        stream = clean_stream()
        stream[0] = init_message(REVIEW_TOOLS + ["Agent"])
        self.assertRejected(stream, reason)
        self.assertRejected(clean_stream()[1:], reason)
        without_list = clean_stream()
        without_list[0] = {"type": "system", "subtype": "init"}
        self.assertRejected(without_list, reason)
        # Without a tool to post findings (or read the diff), "no findings" means nothing.
        for missing in ("mcp__github_inline_comment__create_inline_comment", "Read"):
            with self.subTest(missing=missing):
                stream = [init_message([t for t in REVIEW_TOOLS if t != missing]), final_text(), result()]
                self.assertRejected(stream, reason)
        extra = clean_stream()
        extra[0] = init_message(REVIEW_TOOLS + ["EndConversation"])
        self.assertEqual(self.verify(extra), ("true", "verified"))

    def test_subagent_and_background_task_evidence_is_not_verified(self):
        for subtype in ("task_started", "task_progress", "task_notification", "task_updated", "background_tasks_changed"):
            with self.subTest(subtype=subtype):
                stream = clean_stream()
                stream.insert(3, {"type": "system", "subtype": subtype, "task_id": "a1"})
                self.assertRejected(stream, "background_task_activity")
        stream = clean_stream()
        stream.insert(3, tool_use("s1", "Read", parent="t9"))
        self.assertRejected(stream, "subagent_activity")

    def test_unexpected_tools_are_not_verified(self):
        for name in ("Agent", "EndConversation", "ScheduleWakeup", "Bash", "mcp__github_ci__get_ci_status"):
            with self.subTest(tool=name):
                stream = clean_stream()
                stream[1:3] = [tool_use("t1", name), tool_result("t1")]
                self.assertRejected(stream, "unexpected_tool")

    def test_unfinished_tool_activity_is_not_verified(self):
        unanswered = clean_stream()
        del unanswered[2]
        self.assertRejected(unanswered, "unfinished_tool_use")
        ends_mid_tool = clean_stream()[:-2] + [result()]
        self.assertRejected(ends_mid_tool, "unfinished_tool_use")
        no_turns = [init_message(), result()]
        self.assertRejected(no_turns, "unfinished_tool_use")


if __name__ == "__main__":
    unittest.main()
