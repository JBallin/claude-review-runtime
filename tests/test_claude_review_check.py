"""Regression tests for the packaged review helper.

End-to-end cases run the script against a stub `gh` on PATH that serves
scripted responses (including multi-page and failing ones) and records every
call, so publication, pagination, and fail-closed behavior are exercised
without network access.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "actions" / "review-helper" / "claude_review_check.py"
HEAD = "a" * 40
OTHER = "b" * 40
NEWER = "e" * 40
MERGE_BASE = "c" * 40
BASE_TIP = "d" * 40
REPO = "owner/repo"

spec = importlib.util.spec_from_file_location("claude_review_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

STUB_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    method = args[args.index("--method") + 1] if "--method" in args else "GET"
    path = [a for a in args[1:] if not a.startswith("-") and a not in ("POST", "PATCH", "DELETE")][0]
    body = json.loads(sys.stdin.read()) if "--input" in args else None
    log = os.environ["GH_STUB_LOG"]
    calls = [json.loads(line) for line in open(log)] if os.path.exists(log) else []
    rules = json.load(open(os.environ["GH_STUB_SCENARIO"]))
    for index, rule in enumerate(rules):
        if rule["method"] == method and (path == rule["path"] or path.startswith(rule["path"] + "?")):
            break
    else:
        sys.stderr.write(f"no stub for {method} {path}\\n")
        sys.exit(2)
    seen = sum(1 for call in calls if call["rule"] == index)
    with open(log, "a") as handle:
        handle.write(json.dumps({"rule": index, "method": method, "path": path, "args": args, "body": body}) + "\\n")
    response = rule["responses"][min(seen, len(rule["responses"]) - 1)]
    if response.get("fail"):
        sys.stderr.write("HTTP 502: stubbed failure\\n")
        sys.exit(1)
    sys.stdout.write(response["stdout"])
    """
)


def comment(comment_id, *, login="claude[bot]", user_type="Bot", commit=HEAD):
    return {
        "id": comment_id,
        "user": {"login": login, "type": user_type},
        "commit_id": commit,
        "original_commit_id": commit,
    }


def evidence_text(finding_ids=(), *, pr=7, merge_base=MERGE_BASE):
    evidence = {"new_finding_comment_ids": list(finding_ids)}
    if pr is not None:
        evidence["pr_number"] = pr
    if merge_base is not None:
        evidence["merge_base_sha"] = merge_base
    return "```json\n" + json.dumps(evidence) + "\n```"


def check_run(
    run_id,
    conclusion,
    *,
    status="completed",
    name="Claude Review",
    slug="github-actions",
    pr=7,
    merge_base=MERGE_BASE,
    legacy=False,
):
    """A check run whose evidence identifies this PR and reviewed diff unless legacy."""
    run = {
        "id": run_id,
        "name": name,
        "head_sha": HEAD,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": slug},
    }
    if not legacy:
        run["output"] = {"text": evidence_text(pr=pr, merge_base=merge_base)}
    return run


def evidence_of(body):
    text = body["output"]["text"]
    return json.loads(text[len("```json\n") : -len("\n```")])


def pages(*documents):
    """Render documents the way `gh api --paginate` concatenates pages."""
    return "".join(json.dumps(document) for document in documents)


def ok(stdout):
    return {"stdout": stdout}


FAIL = {"fail": True}


class PureLogicTests(unittest.TestCase):
    def test_conclusion_mapping(self):
        # (review_result, action_conclusion, new_ids, earlier_ids, prior_ids)
        cases = [
            (("success", "success", [], [], []), "success"),
            (("success", "success", [11], [], []), "action_required"),
            (("success", "success", [], [10], []), "action_required"),
            (("success", "success", [], [], [5]), "action_required"),
            (("failure", "", [], [], []), "failure"),
            (("failure", "success", [], [], []), "failure"),
            (("cancelled", "", [], [10], [5]), "failure"),
            (("skipped", "", [], [], []), "failure"),
            (("success", "failure", [11], [], []), "failure"),
            (("success", "", [], [], []), "failure"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(check.decide(*args, completion_verified=True)[0], expected)

    def test_completion_must_be_verified_explicitly(self):
        self.assertEqual(check.decide("success", "success", [], [], [])[0], "failure")

    def test_unverified_completion_never_maps_to_success(self):
        for new_ids, earlier_ids, prior_ids in (([], [], []), ([11], [], []), ([], [10], [5])):
            with self.subTest(new=new_ids, earlier=earlier_ids, prior=prior_ids):
                conclusion, title, sentence = check.decide(
                    "success",
                    "success",
                    new_ids,
                    earlier_ids,
                    prior_ids,
                    completion_verified=False,
                    completion_reason="unexpected_tool",
                )
                self.assertEqual((conclusion, title), ("failure", "Review completion could not be verified"))
                self.assertIn("unexpected_tool", sentence)

    def test_start_check_failure_is_reported_as_a_review_that_did_not_start(self):
        conclusion, title, _ = check.decide("skipped", "", [], [], [], start_result="failure")
        self.assertEqual((conclusion, title), ("failure", "Review did not start"))

    def test_failure_summary_keeps_an_earlier_finding_visible(self):
        conclusion, _, sentence = check.decide("failure", "", [], [], [5])
        self.assertEqual(conclusion, "failure")
        self.assertIn("check run 5", sentence)

    def test_evidence_error_after_a_completed_review_maps_to_failure(self):
        conclusion, title, _ = check.decide("success", "success", [], [], [], "comments unavailable", completion_verified=True)
        self.assertEqual((conclusion, title), ("failure", "Review outcome could not be determined"))

    def test_every_gh_call_is_bounded_by_the_configured_timeout(self):
        completed = subprocess.CompletedProcess(["gh"], 0, stdout="{}", stderr="")
        with mock.patch.object(check.subprocess, "run", return_value=completed) as run:
            check.gh_api(["repos/owner/repo"])
        self.assertEqual(run.call_args.kwargs.get("timeout"), check.GH_TIMEOUT_SECONDS)

    def test_a_timed_out_gh_call_becomes_a_retryable_error(self):
        expired = subprocess.TimeoutExpired(["gh"], check.GH_TIMEOUT_SECONDS)
        with mock.patch.object(check.subprocess, "run", side_effect=expired):
            with self.assertRaises(check.GhError):
                check.gh_api(["repos/owner/repo"])

    def test_sticky_output_describes_recorded_findings_not_open_ones(self):
        _, title, sentence = check.decide("success", "success", [], [10], [5], completion_verified=True)
        self.assertEqual(title, "Earlier findings were recorded")
        self.assertNotIn("still apply", sentence)
        self.assertIn("recorded", sentence)

    def test_comment_filter_scopes_to_claude_and_the_reviewed_commit(self):
        page = [
            comment(1),
            comment(2, login="chatgpt-codex-connector[bot]"),
            comment(3, login="example-user", user_type="User"),
            comment(4, commit=OTHER),
            {**comment(5), "commit_id": OTHER},  # carried forward to a later head
        ]
        self.assertEqual(check.claude_comment_ids([page], HEAD), [1, 5])

    def test_only_completed_github_actions_runs_with_findings_are_sticky(self):
        page = {
            "check_runs": [
                check_run(1, "action_required"),
                check_run(2, "failure"),
                check_run(3, None, status="in_progress"),
                check_run(4, "action_required", slug="other-app"),
                check_run(5, "action_required", name="Something Else"),
                check_run(6, "action_required"),
                {**check_run(7, "failure"), "output": {"text": evidence_text([301])}},
                {**check_run(8, "failure"), "output": {"text": evidence_text([])}},
                {**check_run(9, "failure"), "output": {"text": "not json"}},
            ]
        }
        self.assertEqual(check.prior_finding_evidence([page], HEAD, 6, "7", MERGE_BASE)[0], [1, 7])

    def test_sticky_history_is_scoped_to_the_pr_and_reviewed_diff(self):
        page = {
            "check_runs": [
                check_run(1, "action_required"),
                check_run(2, "action_required", pr=8),
                check_run(3, "action_required", merge_base=OTHER),
                check_run(4, "action_required", legacy=True),
                check_run(5, "action_required", pr=None),
                {**check_run(6, "failure"), "output": {"text": evidence_text([301], pr=None)}},
            ]
        }
        self.assertEqual(check.prior_finding_evidence([page], HEAD, 99, "7", MERGE_BASE)[0], [1])
        self.assertEqual(check.prior_finding_evidence([page], HEAD, 99, "8", MERGE_BASE)[0], [2])
        self.assertEqual(check.prior_finding_evidence([page], HEAD, 99, "7", "")[0], [])

    def test_comment_attribution_requires_trusted_same_pr_head_history(self):
        runs = [
            {**check_run(1, "failure"), "output": {"text": evidence_text([10])}},
            {**check_run(2, "action_required", merge_base=OTHER),
             "output": {"text": evidence_text([10, 20], merge_base=OTHER)}},
            {**check_run(3, "action_required", slug="other-app"),
             "output": {"text": evidence_text([30])}},
            {**check_run(4, "action_required", pr=8),
             "output": {"text": evidence_text([40], pr=8)}},
            {**check_run(5, "action_required"), "head_sha": OTHER,
             "output": {"text": evidence_text([50])}},
            {**check_run(99, "action_required"), "output": {"text": evidence_text([60])}},
            {**check_run(7, "action_required", legacy=True),
             "output": {"text": evidence_text([70], merge_base=None)}},
        ]
        self.assertEqual(
            check.prior_finding_evidence([{"check_runs": runs}], HEAD, 99, "7", MERGE_BASE),
            ([1], {10}, {20}),
        )


class ScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        stub = self.dir / "bin" / "gh"
        stub.parent.mkdir()
        stub.write_text(STUB_GH)
        stub.chmod(0o755)
        self.log = self.dir / "gh.log"
        self.output = self.dir / "github_output"
        self.before = self.dir / "before.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, command, rules, **extra_env):
        scenario = self.dir / "scenario.json"
        if (extra_env.get("STATUS_COMMENTS_ENABLED") == "true" and command[0] != "stale"
                and not any(rule["path"] == f"repos/{REPO}/pulls/7" for rule in rules)):
            rules = [pr_head_rule(live_head(extra_env.get("HEAD_SHA", HEAD))), *rules]
        scenario.write_text(json.dumps(rules))
        environment = {
            "PATH": f"{self.dir / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_LOG": str(self.log),
            "GH_STUB_SCENARIO": str(scenario),
            "GITHUB_OUTPUT": str(self.output),
            "CLAUDE_REVIEW_RETRY_DELAY": "0",
            "REPO": REPO,
            "PR_NUMBER": "7",
            "HEAD_SHA": HEAD,
            "BASE_REF": "main",
            "BASE_SHA": BASE_TIP,
            "RUNTIME_REPOSITORY": "example/review-runtime",
            "RUNTIME_SHA": "f" * 40,
            "RUNTIME_WORKFLOW_PATH": ".github/workflows/claude-review.yml",
            "MERGE_BASE_SHA": MERGE_BASE,
            # A normal finished run; the completion tests override these.
            "COMPLETION_VERIFIED": "true",
            "COMPLETION_REASON": "verified",
            "TRIGGER_LABEL": "Draft marked ready",
            **extra_env,
        }
        return subprocess.run(
            [sys.executable, str(SCRIPT), *command],
            env=environment,
            capture_output=True,
            text=True,
        )

    def calls(self, method=None):
        if not self.log.exists():
            return []
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [call for call in calls if method is None or call["method"] == method]


def comments_rule(*responses):
    return {"method": "GET", "path": f"repos/{REPO}/pulls/7/comments", "responses": list(responses)}


def history_rule(*responses):
    return {"method": "GET", "path": f"repos/{REPO}/commits/{HEAD}/check-runs", "responses": list(responses)}


def patch_rule(*responses):
    return {"method": "PATCH", "path": f"repos/{REPO}/check-runs/99", "responses": list(responses)}


def status_list_rule(*responses):
    return {"method": "GET", "path": f"repos/{REPO}/issues/7/comments", "responses": list(responses)}


def pr_head_rule(*responses):
    return {"method": "GET", "path": f"repos/{REPO}/pulls/7", "responses": list(responses)}


def live_head(sha, base_ref="main"):
    return ok(json.dumps({"head": {"sha": sha}, "base": {"ref": base_ref}}))


def status_create_rule(*responses):
    return {"method": "POST", "path": f"repos/{REPO}/issues/7/comments", "responses": list(responses)}


def status_patch_rule(*responses):
    return {"method": "PATCH", "path": f"repos/{REPO}/issues/comments/55", "responses": list(responses)}


def reaction_list_rule(*responses):
    return {"method": "GET", "path": f"repos/{REPO}/issues/7/reactions", "responses": list(responses)}


def reaction_post_rule(*responses):
    return {"method": "POST", "path": f"repos/{REPO}/issues/7/reactions", "responses": list(responses)}


def reaction_delete_rule(reaction_id, *responses):
    return {"method": "DELETE", "path": f"repos/{REPO}/issues/7/reactions/{reaction_id}", "responses": list(responses)}


def reaction(reaction_id, content, *, login="github-actions[bot]", user_type="User"):
    # GitHub's live PR reaction API reports github-actions[bot] as type User.
    return {"id": reaction_id, "content": content, "user": {"login": login, "type": user_type}}


def status_comment(head=HEAD, *, base_ref="main", login="github-actions[bot]", user_type="Bot", comment_id=55):
    return {
        "id": comment_id,
        "user": {"login": login, "type": user_type},
        "body": check.status_comment_body(head, base_ref, "success"),
    }


def current_run_only():
    return ok(pages({"check_runs": [check_run(99, None, status="in_progress")]}))


class FinalizeTests(ScriptTestCase):
    def finalize(self, rules, *, before=(), review_result="success", action_conclusion="success", **extra):
        self.before.write_text(json.dumps(list(before)))
        return self.run_script(
            ["finalize"],
            rules,
            CHECK_RUN_ID="99",
            REVIEW_RESULT=review_result,
            ACTION_CONCLUSION=action_conclusion,
            BEFORE_IDS_FILE=str(self.before),
            **extra,
        )

    def published(self):
        return self.calls("PATCH")[-1]["body"]

    def test_clean_review_publishes_success_with_the_reviewed_sha(self):
        result = self.finalize(
            [
                history_rule(current_run_only()),
                comments_rule(ok(pages([comment(1, login="example-user", user_type="User")]))),
                patch_rule(ok("{}")),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual((body["status"], body["conclusion"]), ("completed", "success"))
        self.assertIn(HEAD, body["output"]["summary"])
        self.assertIn(f'"reviewed_sha": "{HEAD}"', body["output"]["text"])

    def test_missing_or_unverified_completion_publishes_failure(self):
        for verdict, reason in (("", ""), ("false", "background_task_activity"), ("TRUE", "verified")):
            with self.subTest(verdict=verdict):
                self.log.unlink(missing_ok=True)
                result = self.finalize(
                    [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}"))],
                    COMPLETION_VERIFIED=verdict,
                    COMPLETION_REASON=reason,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                body = self.published()
                self.assertEqual(body["conclusion"], "failure")
                self.assertEqual(body["output"]["title"], "Review completion could not be verified")
                self.assertIs(evidence_of(body)["completion_verified"], False)

    def test_rejected_tool_or_input_evidence_cannot_publish_a_clean_check(self):
        for reason in ("errored_inline_tool_result", "captured_inputs_not_read"):
            with self.subTest(reason=reason):
                self.log.unlink(missing_ok=True)
                result = self.finalize(
                    [history_rule(current_run_only()), comments_rule(ok(pages([]))),
                     patch_rule(ok("{}"))],
                    COMPLETION_VERIFIED="false", COMPLETION_REASON=reason,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                body = self.published()
                self.assertEqual(body["conclusion"], "failure")
                self.assertEqual(body["output"]["title"], "Review completion could not be verified")
                evidence = evidence_of(body)
                self.assertEqual(evidence["start_check_result"], "success")
                self.assertEqual(evidence["review_job_result"], "success")
                self.assertEqual(evidence["action_conclusion"], "success")
                self.assertIs(evidence["completion_verified"], False)
                self.assertEqual(evidence["completion_reason"], reason)
                self.assertEqual(evidence["claude_finding_comment_ids"], [])
                self.assertIn(reason, body["output"]["summary"])

    def test_findings_from_an_unverified_run_are_recorded_and_stay_sticky(self):
        first = self.finalize(
            [history_rule(current_run_only()), comments_rule(ok(pages([comment(9)]))), patch_rule(ok("{}"))],
            COMPLETION_VERIFIED="false",
            COMPLETION_REASON="unfinished_tool_use",
        )
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        first_body = self.published()
        self.assertEqual(first_body["conclusion"], "failure")
        self.assertEqual(evidence_of(first_body)["new_finding_comment_ids"], [9])

        rerun = self.rerun_after(first_body)
        self.assertEqual(rerun.returncode, 0, rerun.stderr + rerun.stdout)
        self.assertEqual(self.published()["conclusion"], "action_required")

    def test_verified_completion_is_recorded_with_a_clean_result(self):
        result = self.finalize(
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}"))]
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        evidence = evidence_of(self.published())
        self.assertEqual((evidence["completion_verified"], evidence["completion_reason"]), (True, "verified"))

    def test_finding_beyond_the_first_page_is_detected(self):
        filler = [comment(i, login="example-user", user_type="User") for i in range(1, 101)]
        noise = [comment(i, login="chatgpt-codex-connector[bot]") for i in range(101, 201)]
        last_page = [comment(201, commit=OTHER), comment(202)]
        result = self.finalize(
            [
                history_rule(current_run_only()),
                comments_rule(ok(pages(filler, noise, last_page))),
                patch_rule(ok("{}")),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "action_required")
        self.assertIn('"new_finding_comment_ids": [\n    202\n  ]', body["output"]["text"])
        comment_call = next(c for c in self.calls("GET") if "/pulls/7/comments" in c["path"])
        self.assertIn("--paginate", comment_call["args"])

    def test_existing_claude_findings_are_not_new_but_keep_the_commit_action_required(self):
        result = self.finalize(
            [
                history_rule(ok(pages({"check_runs": [
                    check_run(99, None, status="in_progress"),
                    {**check_run(98, "failure"), "output": {"text": evidence_text([10, 11])}},
                ]}))),
                comments_rule(ok(pages([comment(10), comment(11)]))),
                patch_rule(ok("{}")),
            ],
            before=[10, 11],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "action_required")
        self.assertEqual(evidence_of(body)["new_finding_comment_ids"], [])
        self.assertEqual(evidence_of(body)["claude_finding_comment_ids"], [10, 11])

    def test_earlier_action_required_stays_sticky_when_it_is_not_the_latest_run(self):
        history = pages(
            {"check_runs": [check_run(99, None, status="in_progress"), check_run(98, "failure")]},
            {"check_runs": [check_run(97, "success"), check_run(42, "action_required")]},
        )
        result = self.finalize(
            [history_rule(ok(history)), comments_rule(ok(pages([]))), patch_rule(ok("{}"))]
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "action_required")
        self.assertIn("check run 42", body["output"]["summary"])
        history_call = next(c for c in self.calls("GET") if "/check-runs" in c["path"])
        self.assertIn("filter=all", history_call["path"])
        self.assertIn("check_name=Claude+Review", history_call["path"])
        self.assertIn("--paginate", history_call["args"])

    def test_an_earlier_failure_is_not_sticky(self):
        history = pages({"check_runs": [check_run(99, None, status="in_progress"), check_run(98, "failure")]})
        result = self.finalize(
            [history_rule(ok(history)), comments_rule(ok(pages([]))), patch_rule(ok("{}"))]
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.published()["conclusion"], "success")

    def test_review_job_failure_before_any_action_output_maps_to_failure(self):
        self.before.unlink(missing_ok=True)
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}"))],
            CHECK_RUN_ID="99",
            REVIEW_RESULT="failure",
            ACTION_CONCLUSION="",
            BEFORE_IDS_FILE=str(self.before),
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "failure")
        self.assertEqual(evidence_of(body)["evidence_errors"], [])

    def test_failed_review_still_records_findings_it_posted(self):
        result = self.finalize(
            [history_rule(current_run_only()), comments_rule(ok(pages([comment(8), comment(9)]))), patch_rule(ok("{}"))],
            before=[8],
            review_result="failure",
            action_conclusion="",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "failure")
        self.assertEqual(evidence_of(body)["new_finding_comment_ids"], [9])
        self.assertEqual(check.recorded_finding_ids(body), [9])
        self.assertEqual(evidence_of(body)["unscoped_finding_comment_ids"], [8])

    def test_findings_from_an_earlier_failed_review_stay_sticky(self):
        earlier = {**check_run(50, "failure"), "output": {"text": evidence_text([9])}}
        history = pages({"check_runs": [check_run(99, None, status="in_progress"), earlier]})
        result = self.finalize(
            # The finding comment is gone; only the earlier run's evidence remains.
            [history_rule(ok(history)), comments_rule(ok(pages([]))), patch_rule(ok("{}"))],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.published()["conclusion"], "action_required")

    def test_post_hook_shaped_failure_maps_to_failure_despite_a_success_output(self):
        result = self.finalize(
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}"))],
            review_result="failure",
            action_conclusion="success",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.published()["conclusion"], "failure")

    def test_unreadable_finding_evidence_fails_closed(self):
        result = self.finalize(
            [history_rule(current_run_only()), comments_rule(FAIL), patch_rule(ok("{}"))]
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.published()["conclusion"], "failure")

    def rerun_after(self, first_body, *, comments=(9,)):
        """A later clean review of the same commit, after a run that published first_body."""
        self.log.unlink(missing_ok=True)
        earlier = {**check_run(98, first_body["conclusion"]), "output": first_body["output"]}
        history = pages({"check_runs": [check_run(99, None, status="in_progress"), earlier]})
        existing = [comment(i) for i in comments]
        return self.finalize(
            [history_rule(ok(history)), comments_rule(ok(pages(existing))), patch_rule(ok("{}"))],
            before=comments,
        )

    def test_history_failure_does_not_prevent_recording_comment_evidence(self):
        result = self.finalize(
            [history_rule(FAIL), comments_rule(ok(pages([comment(9)]))), patch_rule(ok("{}"))]
        )
        self.assertEqual(result.returncode, 1)
        body = self.published()
        self.assertEqual(body["conclusion"], "failure")
        evidence = evidence_of(body)
        self.assertEqual(evidence["new_finding_comment_ids"], [9])
        self.assertEqual(evidence["claude_finding_comment_ids"], [9])
        self.assertEqual(len(evidence["evidence_errors"]), 1)
        self.assertEqual(check.recorded_finding_ids(body), [9])

    def test_finding_survives_a_history_failure_into_a_clean_rerun(self):
        first = self.finalize(
            [history_rule(FAIL), comments_rule(ok(pages([comment(9)]))), patch_rule(ok("{}"))]
        )
        self.assertEqual(first.returncode, 1)
        first_body = self.published()
        self.assertEqual(first_body["conclusion"], "failure")

        rerun = self.rerun_after(first_body)
        self.assertEqual(rerun.returncode, 0, rerun.stderr + rerun.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "action_required")
        self.assertEqual(evidence_of(body)["prior_finding_check_run_ids"], [98])

    def test_unattributed_comment_after_comment_read_failure_fails_closed(self):
        first = self.finalize(
            [history_rule(current_run_only()), comments_rule(FAIL), patch_rule(ok("{}"))]
        )
        self.assertEqual(first.returncode, 1)
        first_body = self.published()
        self.assertEqual(first_body["conclusion"], "failure")
        self.assertEqual(check.recorded_finding_ids(first_body), [])

        # A head-only comment reveals a finding, but cannot identify its diff.
        rerun = self.rerun_after(first_body)
        self.assertEqual(rerun.returncode, 1, rerun.stderr + rerun.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "failure")
        self.assertEqual(evidence_of(body)["prior_finding_check_run_ids"], [])
        self.assertEqual(evidence_of(body)["claude_finding_comment_ids"], [])
        self.assertEqual(evidence_of(body)["unscoped_finding_comment_ids"], [9])

    def test_transient_publish_failure_is_retried(self):
        result = self.finalize(
            [
                history_rule(current_run_only()),
                comments_rule(ok(pages([]))),
                patch_rule(FAIL, FAIL, ok("{}")),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        patches = self.calls("PATCH")
        self.assertEqual(len(patches), 3)
        self.assertEqual(patches[-1]["body"]["conclusion"], "success")

    def test_persistent_publish_failure_falls_back_then_fails_the_step(self):
        result = self.finalize(
            [
                history_rule(current_run_only()),
                comments_rule(ok(pages([comment(5)]))),
                patch_rule(FAIL, FAIL, FAIL, ok("{}")),
            ]
        )
        self.assertEqual(result.returncode, 1)
        patches = self.calls("PATCH")
        self.assertEqual(len(patches), 4)
        self.assertEqual(patches[0]["body"]["conclusion"], "action_required")
        self.assertEqual(patches[-1]["body"]["conclusion"], "failure")
        self.assertEqual(patches[-1]["body"]["output"]["title"], "Review outcome could not be published")

    def test_unpublishable_state_fails_the_step_after_bounded_attempts(self):
        result = self.finalize(
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(FAIL)]
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls("PATCH")), 2 * check.ATTEMPTS)

    def test_missing_snapshot_after_a_completed_review_fails_closed(self):
        self.before.unlink(missing_ok=True)
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([comment(9)]))), patch_rule(ok("{}"))],
            CHECK_RUN_ID="99",
            REVIEW_RESULT="success",
            ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before),
        )
        self.assertEqual(result.returncode, 1)
        body = self.published()
        self.assertEqual(body["conclusion"], "failure")
        self.assertEqual(evidence_of(body)["claude_finding_comment_ids"], [])
        self.assertEqual(evidence_of(body)["observed_head_comment_ids"], [9])
        self.assertEqual(evidence_of(body)["unscoped_finding_comment_ids"], [9])

    def test_finalize_recovers_an_unreported_check_run_by_external_id(self):
        orphan = {**check_run(99, None, status="in_progress"), "external_id": "Claude Review/5/1"}
        result = self.run_script(
            ["finalize"],
            [history_rule(ok(pages({"check_runs": [orphan]}))), comments_rule(ok(pages([]))), patch_rule(ok("{}"))],
            CHECK_RUN_ID="",
            EXTERNAL_ID="Claude Review/5/1",
            REVIEW_RESULT="skipped",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.published()["conclusion"], "failure")

    def test_fork_without_a_check_run_skips_publication(self):
        fork = self.run_script(["finalize"], [], CHECK_RUN_ID="", IS_FORK="true", REVIEW_RESULT="success")
        self.assertEqual(fork.returncode, 0, fork.stderr + fork.stdout)
        self.assertEqual(self.calls(), [])

    def test_review_that_never_got_a_check_run_still_records_failure_on_its_head(self):
        post = {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok(json.dumps({"id": 5}))]}
        result = self.run_script(
            ["finalize"],
            [history_rule(ok(pages({"check_runs": []}))), post],
            CHECK_RUN_ID="",
            EXTERNAL_ID="Claude Review/5/1",
            START_RESULT="failure",
            REVIEW_RESULT="skipped",
        )
        self.assertEqual(result.returncode, 1)
        body = self.calls("POST")[0]["body"]
        self.assertEqual(
            (body["head_sha"], body["status"], body["conclusion"], body["external_id"]),
            (HEAD, "completed", "failure", "Claude Review/5/1"),
        )
        self.assertEqual(body["output"]["title"], "Review did not start")

    def test_unresolved_external_id_lookup_never_posts_a_new_check_run(self):
        post = {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok(json.dumps({"id": 5}))]}
        result = self.run_script(
            ["finalize"],
            [history_rule(FAIL), post],
            CHECK_RUN_ID="",
            EXTERNAL_ID="Claude Review/5/1",
            START_RESULT="failure",
            REVIEW_RESULT="skipped",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls("GET")), check.ATTEMPTS)
        self.assertEqual(self.calls("POST"), [])
        self.assertIn("Could not confirm whether start-check created", result.stdout)

    def test_unresolved_check_lookup_clears_obsolete_pr_reaction_when_possible(self):
        result = self.run_script(
            ["finalize"],
            [history_rule(FAIL), pr_head_rule(live_head(HEAD)),
             status_list_rule(ok(pages([]))), status_create_rule(ok('{"id":55}')),
             reaction_list_rule(ok(pages([reaction(70, "+1")]))),
             reaction_delete_rule(70, ok(""))],
            CHECK_RUN_ID="", EXTERNAL_ID="Claude Review/5/1",
            STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="manual",
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(any(call["path"] == f"repos/{REPO}/check-runs"
                             for call in self.calls("POST")))
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])

    def test_unresolved_lookup_for_a_fork_stays_best_effort(self):
        post = {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok(json.dumps({"id": 5}))]}
        result = self.run_script(
            ["finalize"],
            [history_rule(FAIL), post],
            CHECK_RUN_ID="",
            EXTERNAL_ID="Claude Review/5/1",
            IS_FORK="true",
            REVIEW_RESULT="success",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.calls("POST"), [])

    def test_failure_before_the_head_sha_is_known_cannot_publish_state(self):
        result = self.run_script(["finalize"], [], CHECK_RUN_ID="", HEAD_SHA="", START_RESULT="failure")
        self.assertEqual(result.returncode, 1)
        self.assertIn("head SHA was never resolved", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_unknown_head_fails_before_any_lookup_even_with_an_external_id(self):
        # The workflows always pass EXTERNAL_ID, even when PR resolution failed.
        rules = [
            {"method": "GET", "path": f"repos/{REPO}/commits/", "responses": [ok(pages({"check_runs": []}))]},
            {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok(json.dumps({"id": 5}))]},
        ]
        result = self.run_script(
            ["finalize"],
            rules,
            CHECK_RUN_ID="",
            HEAD_SHA="",
            EXTERNAL_ID="Claude/5/1",
            START_RESULT="failure",
            REVIEW_RESULT="skipped",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])
        self.assertIn("head SHA was never resolved", result.stdout)
        self.assertNotIn("Could not confirm", result.stdout)

    def test_input_failure_after_check_creation_completes_that_check_as_failure(self):
        self.before.unlink(missing_ok=True)
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}"))],
            CHECK_RUN_ID="99",
            START_RESULT="failure",
            REVIEW_RESULT="skipped",
            BEFORE_IDS_FILE=str(self.before),
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual((body["conclusion"], body["output"]["title"]), ("failure", "Review did not start"))


    # Sticky history is scoped to the reviewed PR and diff.
    def finalize_with_history(self, *runs):
        history = pages({"check_runs": [check_run(99, None, status="in_progress"), *runs]})
        result = self.finalize(
            [history_rule(ok(history)), comments_rule(ok(pages([]))), patch_rule(ok("{}"))]
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return self.published()

    def test_a_finding_in_another_pr_on_the_same_commit_does_not_stick(self):
        self.assertEqual(self.finalize_with_history(check_run(40, "action_required", pr=8))["conclusion"], "success")

    def test_a_finding_in_the_same_pr_and_diff_sticks(self):
        self.assertEqual(self.finalize_with_history(check_run(41, "action_required"))["conclusion"], "action_required")

    def test_a_finding_against_a_different_merge_base_does_not_stick(self):
        stale = check_run(42, "action_required", merge_base=OTHER)
        self.assertEqual(self.finalize_with_history(stale)["conclusion"], "success")

    def test_same_head_retarget_ignores_comments_attributed_only_to_the_old_diff(self):
        earlier = {**check_run(98, "action_required", merge_base=OTHER),
                   "output": {"text": evidence_text([9], merge_base=OTHER)}}
        history = pages({"check_runs": [check_run(99, None, status="in_progress"), earlier]})
        result = self.finalize(
            [history_rule(ok(history)), comments_rule(ok(pages([comment(9)]))), patch_rule(ok("{}"))],
            before=[9],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "success")
        self.assertEqual(evidence_of(body)["claude_finding_comment_ids"], [])
        self.assertEqual(evidence_of(body)["other_diff_finding_comment_ids"], [9])
        self.assertEqual(check.recorded_finding_ids(body), [])
        history_calls = [c for c in self.calls("GET") if "/check-runs" in c["path"]]
        self.assertEqual(len(history_calls), 1)

        # A failed retargeted run must not relabel the old diff's IDs either.
        self.log.unlink()
        failed = self.finalize(
            [history_rule(ok(history)), comments_rule(ok(pages([comment(9), comment(10)]))),
             patch_rule(ok("{}"))], before=[9], review_result="failure",
        )
        self.assertEqual(failed.returncode, 0, failed.stderr + failed.stdout)
        failed_body = self.published()
        self.assertEqual(failed_body["conclusion"], "failure")
        self.assertEqual(check.recorded_finding_ids(failed_body), [10])
        self.assertEqual(evidence_of(failed_body)["other_diff_finding_comment_ids"], [9])

    def test_publish_fallback_preserves_only_same_diff_findings_on_retry(self):
        old_diff = {**check_run(97, "action_required", merge_base=OTHER),
                    "output": {"text": evidence_text([9], merge_base=OTHER)}}
        first = self.finalize(
            [history_rule(ok(pages({"check_runs": [old_diff]}))),
             comments_rule(ok(pages([comment(9), comment(10)]))),
             patch_rule(FAIL, FAIL, FAIL, ok("{}"))], before=[9],
        )
        self.assertEqual(first.returncode, 1)
        fallback = self.published()
        self.assertEqual(fallback["conclusion"], "failure")
        self.assertEqual(check.recorded_finding_ids(fallback), [10])
        self.assertEqual(evidence_of(fallback)["other_diff_finding_comment_ids"], [9])

        self.log.unlink()
        earlier = {**check_run(98, "failure"), "output": fallback["output"]}
        result = self.finalize(
            [history_rule(ok(pages({"check_runs": [old_diff, earlier]}))),
             comments_rule(ok(pages([comment(9)]))), patch_rule(ok("{}"))], before=[9],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "action_required")
        self.assertEqual(check.recorded_finding_ids(body), [10])
        self.assertEqual(evidence_of(body)["other_diff_finding_comment_ids"], [9])

    def test_unattributed_preexisting_comment_fails_closed_without_persisting_false_scope(self):
        result = self.finalize(
            [history_rule(current_run_only()), comments_rule(ok(pages([comment(9)]))),
             patch_rule(ok("{}"))], before=[9],
        )
        self.assertEqual(result.returncode, 1)
        first_body = self.published()
        self.assertEqual(first_body["conclusion"], "failure")
        self.assertEqual(check.recorded_finding_ids(first_body), [])
        self.assertEqual(evidence_of(first_body)["unscoped_finding_comment_ids"], [9])

        # The next run cannot treat the earlier failure's raw observation as
        # authoritative evidence of a finding on its own captured diff.
        rerun = self.rerun_after(first_body)
        self.assertEqual(rerun.returncode, 1)
        self.assertEqual(self.published()["conclusion"], "failure")
        self.assertEqual(check.recorded_finding_ids(self.published()), [])

    def test_same_diff_recorded_ids_survive_comment_deletion_and_a_failed_rerun(self):
        earlier = {**check_run(98, "failure"), "output": {"text": evidence_text([9])}}
        result = self.finalize(
            [history_rule(ok(pages({"check_runs": [earlier]}))), comments_rule(ok(pages([]))),
             patch_rule(ok("{}"))], review_result="failure",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.published()
        self.assertEqual(body["conclusion"], "failure")
        self.assertEqual(check.recorded_finding_ids(body), [9])
        rerun = self.rerun_after(body, comments=())
        self.assertEqual(rerun.returncode, 0, rerun.stderr + rerun.stdout)
        self.assertEqual(self.published()["conclusion"], "action_required")
        self.assertEqual(check.recorded_finding_ids(self.published()), [9])

    def test_history_loss_with_preexisting_comments_fails_closed_without_assuming_the_diff(self):
        result = self.finalize(
            [history_rule(FAIL), comments_rule(ok(pages([comment(9), comment(10)]))),
             patch_rule(ok("{}"))], before=[9],
        )
        self.assertEqual(result.returncode, 1)
        body = self.published()
        self.assertEqual(body["conclusion"], "failure")
        self.assertEqual(check.recorded_finding_ids(body), [10])
        self.assertEqual(evidence_of(body)["unscoped_finding_comment_ids"], [9])

    def test_base_branch_movement_alone_keeps_the_same_diff_sticky(self):
        earlier = {**check_run(43, "action_required")}
        evidence = json.loads(earlier["output"]["text"][len("```json\n") : -len("\n```")])
        earlier["output"] = {"text": "```json\n" + json.dumps({**evidence, "base_sha": OTHER}) + "\n```"}
        self.assertEqual(self.finalize_with_history(earlier)["conclusion"], "action_required")

    def test_legacy_evidence_without_pr_identity_does_not_stick(self):
        legacy = [check_run(44, "action_required", legacy=True), check_run(45, "action_required", pr=None)]
        self.assertEqual(self.finalize_with_history(*legacy)["conclusion"], "success")

    def test_evidence_records_the_reviewed_pr_and_diff_identity(self):
        evidence = evidence_of(self.finalize_with_history())
        self.assertEqual(
            (evidence["reviewed_sha"], evidence["pr_number"], evidence["base_sha"], evidence["merge_base_sha"]),
            (HEAD, 7, BASE_TIP, MERGE_BASE),
        )
        self.assertEqual(evidence["runtime_repository"], "example/review-runtime")
        self.assertEqual(evidence["runtime_sha"], "f" * 40)
        self.assertEqual(evidence["runtime_workflow_path"], ".github/workflows/claude-review.yml")


class WorstCaseModelTests(ScriptTestCase):
    """Drive each command's slowest path and compare its calls with check.WORST_CASE."""

    def test_create_worst_case_matches_the_model(self):
        rules = [
            {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [FAIL]},
            history_rule(ok(pages({"check_runs": []}))),
        ]
        result = self.run_script(["create"], rules, EXTERNAL_ID="Claude Review/5/1")
        self.assertEqual(result.returncode, 1)
        methods = [call["method"] for call in self.calls()]
        self.assertEqual(methods, ["POST", "GET", "POST", "GET", "POST"])
        self.assertEqual(len(methods), check.WORST_CASE["create"][0])

    def test_snapshot_worst_case_matches_the_model(self):
        result = self.run_script(["snapshot", "--output", str(self.dir / "x.json")], [comments_rule(FAIL)])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls()), check.WORST_CASE["snapshot"][0])

    def test_finalize_worst_case_matches_the_model(self):
        found = ok(pages({"check_runs": [{**check_run(99, None, status="in_progress"), "external_id": "X/1/1"}]}))
        self.before.write_text("[]")
        rules = [
            # Lookup fails twice, then finds the run; the history read then fails throughout.
            history_rule(FAIL, FAIL, found, FAIL, FAIL, FAIL),
            comments_rule(FAIL),
            patch_rule(FAIL),
        ]
        result = self.run_script(
            ["finalize"],
            rules,
            CHECK_RUN_ID="",
            EXTERNAL_ID="X/1/1",
            REVIEW_RESULT="success",
            ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before),
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls()), check.WORST_CASE["finalize"][0])

    def test_stale_worst_case_matches_the_model(self):
        result = self.run_script(
            ["stale"],
            [
                pr_head_rule(FAIL, FAIL, live_head(OTHER),
                             FAIL, FAIL, live_head(OTHER)),
                status_list_rule(FAIL, FAIL, ok(pages([status_comment(HEAD)]))),
                status_patch_rule(FAIL, FAIL, ok("{}")),
                reaction_list_rule(ok(pages([reaction(70, "eyes"), reaction(71, "+1")]))),
                reaction_delete_rule(70, ok("")), reaction_delete_rule(71, ok("")),
            ],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="stale",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), check.WORST_CASE["stale"][0])

    def test_status_worst_case_includes_reaction_projection(self):
        result = self.run_script(
            ["create"],
            [
                {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
                pr_head_rule(FAIL, FAIL, live_head(HEAD), FAIL, FAIL, live_head(HEAD)),
                status_list_rule(FAIL, FAIL, ok(pages([status_comment(HEAD)]))),
                status_patch_rule(FAIL, FAIL, ok("{}")),
                reaction_list_rule(ok(pages([reaction(70, "+1")]))),
                reaction_delete_rule(70, ok("")), reaction_post_rule(ok('{"id":80}')),
            ],
            STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            len([call for call in self.calls() if call["path"] == f"repos/{REPO}/pulls/7"]),
            2 * check.ATTEMPTS,
        )
        self.assertEqual(check.WORST_CASE["status"][1], 4)
        self.assertEqual(len(self.calls()) - 1, check.WORST_CASE["status"][0])


class CreateAndSnapshotTests(ScriptTestCase):
    create_rule = {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": []}

    def create(self, responses, **extra):
        rule = {**self.create_rule, "responses": responses}
        return self.run_script(["create"], [rule], **extra)

    def test_create_opens_an_in_progress_check_on_the_reviewed_sha(self):
        result = self.create([ok(json.dumps({"id": 99}))])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        body = self.calls("POST")[0]["body"]
        self.assertEqual((body["name"], body["head_sha"], body["status"]), ("Claude Review", HEAD, "in_progress"))
        self.assertEqual(self.output.read_text(), "check_run_id=99\n")

    def test_failed_looking_create_reuses_the_run_it_already_made(self):
        existing = {**check_run(77, None, status="in_progress"), "external_id": "Claude Review/5/1"}
        other = {**check_run(76, None, status="in_progress"), "external_id": "Claude Review/4/1"}
        rules = [
            {**self.create_rule, "responses": [FAIL]},
            {**history_rule(ok(pages({"check_runs": [other]}, {"check_runs": [existing]})))},
        ]
        result = self.run_script(["create"], rules, EXTERNAL_ID="Claude Review/5/1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(self.calls("POST")), 1)
        self.assertEqual(self.output.read_text(), "check_run_id=77\n")

    def test_ambiguous_create_reconciles_through_a_failed_lookup_without_reposting(self):
        existing = {**check_run(77, None, status="in_progress"), "external_id": "Claude Review/5/1"}
        rules = [
            {**self.create_rule, "responses": [FAIL, ok(json.dumps({"id": 78}))]},
            history_rule(FAIL, ok(pages({"check_runs": [existing]}))),
        ]
        result = self.run_script(["create"], rules, EXTERNAL_ID="Claude Review/5/1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(self.calls("POST")), 1)
        self.assertEqual(self.output.read_text(), "check_run_id=77\n")

    def test_create_posts_again_only_after_confirming_no_run_exists(self):
        rules = [
            {**self.create_rule, "responses": [FAIL, ok(json.dumps({"id": 78}))]},
            history_rule(ok(pages({"check_runs": []}))),
        ]
        result = self.run_script(["create"], rules, EXTERNAL_ID="Claude Review/5/1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        methods = [call["method"] for call in self.calls()]
        self.assertEqual(methods, ["POST", "GET", "POST"])
        self.assertEqual(self.output.read_text(), "check_run_id=78\n")

    def test_same_repo_create_failure_fails_closed_without_blind_reposts(self):
        result = self.create([FAIL], EXTERNAL_ID="Claude Review/5/1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.calls("POST")), 1)
        self.assertFalse(self.output.exists())

    def test_fork_create_failure_does_not_block_the_review(self):
        result = self.create([FAIL], IS_FORK="true", EXTERNAL_ID="Claude Review/5/1")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.output.read_text(), "check_run_id=\n")

    def test_snapshot_records_every_claude_comment_on_the_reviewed_sha(self):
        target = self.dir / "evidence" / "before.json"
        first = [comment(i, login="example-user", user_type="User") for i in range(1, 101)]
        result = self.run_script(
            ["snapshot", "--output", str(target)],
            [comments_rule(ok(pages(first, [comment(150), comment(151, commit=OTHER)])))],
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(target.read_text()), [150])

    def test_snapshot_failure_fails_the_step(self):
        result = self.run_script(["snapshot", "--output", str(self.dir / "x.json")], [comments_rule(FAIL)])
        self.assertEqual(result.returncode, 1)


class StatusCommentTests(ScriptTestCase):
    def test_start_creates_one_comment_after_the_check_for_all_triggers(self):
        for label in ("Manual request", "Draft marked ready", "PR opened for review"):
            with self.subTest(label=label):
                self.log.unlink(missing_ok=True)
                result = self.run_script(
                    ["create"],
                    [
                        {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
                        status_list_rule(ok(pages([]))),
                        status_create_rule(ok('{"id":55}')),
                    ],
                    STATUS_COMMENTS_ENABLED="true", TRIGGER_LABEL=label,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual([c["method"] for c in self.calls()], ["POST", "GET", "GET", "POST"])
                body = self.calls("POST")[-1]["body"]["body"]
                self.assertIn(HEAD, body)
                self.assertEqual(check.status_base_ref({"body": body}), "main")
                self.assertIn("in progress", body)
                self.assertIn(f"**Current commit:** `{HEAD[:7]}`\n**Trigger:** {label}\n", body)
                self.assertNotIn("Authority:", body)
                self.assertNotIn("@claude", body)
                self.assertIn("status_comment_id=55", self.output.read_text())

    def test_existing_owned_marker_is_updated_while_unrelated_copies_are_ignored(self):
        comments = [
            {**status_comment(comment_id=51), "body": "an unrelated bot comment"},
            status_comment(comment_id=52, login="example-user", user_type="User"),
            status_comment(OTHER),
        ]
        result = self.run_script(
            ["create"],
            [
                {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
                status_list_rule(ok(pages(comments))),
                status_patch_rule(ok("{}")),
            ],
            STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.calls("POST")), 1)  # only the authoritative Check
        self.assertEqual(self.calls("PATCH")[0]["path"], f"repos/{REPO}/issues/comments/55")

    def test_same_head_rerun_updates_the_existing_comment_without_duplicate(self):
        result = self.run_script(
            ["create"],
            [
                {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
                status_list_rule(ok(pages([status_comment(HEAD)]))),
                status_patch_rule(ok("{}")),
            ],
            STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("POST")), 1)
        self.assertEqual(len(self.calls("PATCH")), 1)

    def test_superseded_review_never_creates_or_overwrites_current_status(self):
        for command, comments in (("create", [status_comment(NEWER)]), ("create", [])):
            with self.subTest(command=command, comments=comments):
                self.log.unlink(missing_ok=True)
                result = self.run_script(
                    [command],
                    [pr_head_rule(live_head(NEWER)),
                     {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
                     status_list_rule(ok(pages(comments))), status_patch_rule(ok("{}")),
                     status_create_rule(ok('{"id":55}'))],
                    STATUS_COMMENTS_ENABLED="true",
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual([call["path"] for call in self.calls()],
                                 [f"repos/{REPO}/check-runs", f"repos/{REPO}/pulls/7"])

    def test_superseded_rerun_preserves_newer_status_after_check_finalization(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [pr_head_rule(live_head(NEWER)), history_rule(current_run_only()),
             comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([status_comment(NEWER)]))), status_patch_rule(ok("{}"))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
            PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual([call["path"] for call in self.calls("PATCH")],
                         [f"repos/{REPO}/check-runs/99"])
        self.assertFalse(any("reactions" in call["path"] for call in self.calls()))

    def test_old_base_review_cannot_overwrite_same_head_on_new_base(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [pr_head_rule(live_head(HEAD, "release")), history_rule(current_run_only()),
             comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([status_comment(HEAD, base_ref="release")]))),
             status_patch_rule(ok("{}"))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual([call["path"] for call in self.calls("PATCH")],
                         [f"repos/{REPO}/check-runs/99"])

    def test_live_base_lookup_failure_skips_comment_without_changing_check(self):
        for live in (ok(json.dumps({"head": {"sha": HEAD}, "base": None})), FAIL):
            with self.subTest(live=live):
                self.log.unlink(missing_ok=True)
                result = self.run_script(
                    ["create"],
                    [pr_head_rule(live),
                     {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
                     status_list_rule(ok(pages([])))],
                    STATUS_COMMENTS_ENABLED="true",
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(len(self.calls("POST")), 1)
                self.assertEqual(self.calls("PATCH"), [])

    def test_unavailable_live_head_skips_comment_without_changing_check(self):
        result = self.run_script(
            ["create"],
            [pr_head_rule(FAIL),
             {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
             status_list_rule(ok(pages([])))],
            STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("POST")), 1)
        self.assertEqual(len(self.calls("GET")), check.ATTEMPTS)

    def test_nullable_comment_user_and_local_projection_error_are_best_effort(self):
        result = self.run_script(
            ["create"],
            [{"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
             status_list_rule(ok(pages([{"id": 8, "user": None, "body": check.STATUS_MARKER}]))),
             status_create_rule(ok('{"id":55}'))],
            STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("POST")), 2)
        with mock.patch.object(check, "update_status_comment", side_effect=RuntimeError("local error")):
            with mock.patch.object(check, "live_pr_identity", return_value=(HEAD, "main")):
                with mock.patch.dict(os.environ, {"STATUS_COMMENTS_ENABLED": "true", "PR_NUMBER": "7",
                                                  "REPO": REPO, "BASE_REF": "main"}):
                    check.best_effort_status(HEAD, "success")

    def test_ambiguous_status_create_is_never_reposted_and_check_stays_successful(self):
        result = self.run_script(
            ["create"],
            [
                {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
                status_list_rule(ok(pages([]))),
                status_create_rule(FAIL),
            ],
            STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls("POST")), 2)
        self.assertIn("Could not publish", result.stdout)

    def test_comment_list_or_update_failure_does_not_change_authoritative_result(self):
        for status_rule in (status_list_rule(FAIL), status_list_rule(ok(pages([status_comment()]))) ):
            with self.subTest(rule=status_rule["responses"]):
                self.log.unlink(missing_ok=True)
                rules = [
                    history_rule(current_run_only()),
                    comments_rule(ok(pages([]))),
                    patch_rule(ok("{}")),
                    status_rule,
                    status_patch_rule(FAIL),
                ]
                self.before.write_text("[]")
                result = self.run_script(
                    ["finalize"], rules,
                    CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                    BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], "success")

    def test_final_status_projects_clean_findings_sticky_and_failure(self):
        cases = [
            ([], [], "success", "no findings", {}),
            ([comment(9)], [], "action_required", "inline review threads", {}),
            ([], [check_run(41, "action_required")], "action_required", "inline review threads", {}),
            ([], [], "failure", "❌ Claude Review incomplete", {"REVIEW_RESULT": "failure"}),
            ([], [], "failure", "❌ Claude Review incomplete", {"ACTION_CONCLUSION": "failure"}),
            ([], [], "failure", "❌ Claude Review incomplete", {"COMPLETION_VERIFIED": "false"}),
        ]
        for inline, prior, expected, phrase, override in cases:
            with self.subTest(expected=expected, prior=prior, override=override):
                self.log.unlink(missing_ok=True)
                self.before.write_text("[]")
                result = self.run_script(
                    ["finalize"],
                    [
                        history_rule(ok(pages({"check_runs": [check_run(99, None, status="in_progress"), *prior]}))),
                        comments_rule(ok(pages(inline))),
                        patch_rule(ok("{}")),
                        status_list_rule(ok(pages([status_comment()]))),
                        status_patch_rule(ok("{}")),
                    ],
                    **{
                        "CHECK_RUN_ID": "99", "REVIEW_RESULT": "success", "ACTION_CONCLUSION": "success",
                        "BEFORE_IDS_FILE": str(self.before), "STATUS_COMMENTS_ENABLED": "true", **override,
                    },
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], expected)
                body = self.calls("PATCH")[-1]["body"]["body"]
                self.assertIn(phrase, body)
                self.assertIn(HEAD, body)
                self.assertIn(f"**Current commit:** `{HEAD[:7]}`\n**Trigger:** Draft marked ready\n", body)
                self.assertNotIn("unresolved", body)

    def test_failed_check_publication_projects_fallback_or_unknown_state(self):
        for fallback_ok, phrase in ((True, "did not complete reliably"), (False, "could not be published")):
            with self.subTest(fallback_ok=fallback_ok):
                self.log.unlink(missing_ok=True)
                self.before.write_text("[]")
                result = self.run_script(
                    ["finalize"],
                    [
                        history_rule(current_run_only()), comments_rule(ok(pages([]))),
                        patch_rule(*( [FAIL] * check.ATTEMPTS + ([ok("{}")] if fallback_ok else []))),
                        status_list_rule(ok(pages([status_comment()]))), status_patch_rule(ok("{}")),
                    ],
                    CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
                    BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(phrase, self.calls("PATCH")[-1]["body"]["body"])

    def test_stale_updates_only_an_older_owned_comment_when_event_head_is_live(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(OTHER)), status_list_rule(ok(pages([status_comment(HEAD)]))),
             status_patch_rule(ok("{}"))],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        body = self.calls("PATCH")[0]["body"]["body"]
        self.assertIn(OTHER, body)
        self.assertIn("not covered", body)
        self.assertEqual(check.status_head({"body": body}), OTHER)

    def test_base_ref_edit_stales_prior_status_even_when_head_is_unchanged(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(HEAD, "release")),
             status_list_rule(ok(pages([status_comment(HEAD, base_ref="main")]))),
             status_patch_rule(ok("{}"))],
            BASE_REF="release", STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        body = self.calls("PATCH")[0]["body"]["body"]
        self.assertIn("not covered", body)
        self.assertEqual(check.status_head({"body": body}), HEAD)
        self.assertEqual(check.status_base_ref({"body": body}), "release")

    def test_older_base_edit_cannot_overwrite_new_base_status(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(HEAD, "release")),
             status_list_rule(ok(pages([status_comment(HEAD, base_ref="release")]))),
             status_patch_rule(ok("{}"))],
            BASE_REF="main", STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual([call["path"] for call in self.calls()], [f"repos/{REPO}/pulls/7"])

    def test_older_synchronize_event_cannot_overwrite_newer_head_status(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(NEWER)), status_list_rule(ok(pages([status_comment(NEWER)]))),
             status_patch_rule(ok("{}"))],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("superseded", result.stdout)
        self.assertEqual([call["path"] for call in self.calls()], [f"repos/{REPO}/pulls/7"])

    def test_stale_is_a_noop_without_a_comment_or_when_newer_review_matches_head(self):
        for comments in ([], [status_comment(OTHER)]):
            with self.subTest(comments=comments):
                self.log.unlink(missing_ok=True)
                result = self.run_script(
                    ["stale"],
                    [pr_head_rule(live_head(OTHER)), status_list_rule(ok(pages(comments)))],
                    HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true",
                )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(self.calls("POST") + self.calls("PATCH"), [])

    def test_unavailable_live_head_is_best_effort_and_never_mutates_status(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(FAIL), status_list_rule(ok(pages([status_comment(HEAD)]))),
             status_patch_rule(ok("{}"))],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true", IS_FORK="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), check.ATTEMPTS)
        self.assertEqual(self.calls("PATCH"), [])

    def test_fork_check_and_comment_failures_remain_best_effort(self):
        result = self.run_script(
            ["create"],
            [
                {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [FAIL]},
                status_list_rule(FAIL),
            ],
            IS_FORK="true", STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("check_run_id=", self.output.read_text())

    def test_fork_without_check_can_publish_accurately_scoped_comment(self):
        result = self.run_script(
            ["create"],
            [
                {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [FAIL]},
                status_list_rule(ok(pages([]))),
                status_create_rule(ok('{"id":55}')),
            ],
            IS_FORK="true", STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        body = self.calls("POST")[-1]["body"]["body"]
        self.assertIn("Check publication is unavailable", body)
        self.assertIn(HEAD, body)

    def test_fork_finalization_without_check_does_not_claim_a_review_result(self):
        result = self.run_script(
            ["finalize"],
            [status_list_rule(ok(pages([status_comment(HEAD)]))), status_patch_rule(ok("{}"))],
            CHECK_RUN_ID="", IS_FORK="true", STATUS_COMMENTS_ENABLED="true",
        )
        self.assertEqual(result.returncode, 0)
        body = self.calls("PATCH")[0]["body"]["body"]
        self.assertIn("Review completion cannot be established", body)
        self.assertIn("Check publication is unavailable", body)

    def test_all_status_messages_are_exact_sha_scoped_without_a_known_trigger(self):
        for state in ("in_progress", "success", "action_required", "failure", "publication_incomplete", "stale"):
            for available in (True, False):
                body = check.status_comment_body(HEAD, "main", state, check_available=available)
                self.assertIn(HEAD, body)
                self.assertNotIn("@claude", body)
                self.assertNotIn("This comment is informational", body)
                self.assertNotIn("Authority:", body)
                self.assertNotIn("**Trigger:**", body)
                if state in ("in_progress", "success", "action_required", "failure", "stale") and available:
                    self.assertNotIn("Check", body)
        for state in ("in_progress", "publication_incomplete"):
            body = check.status_comment_body(HEAD, "main", state, check_available=False)
            self.assertIn("authoritative Check publication is unavailable", body)
        incomplete = check.status_comment_body(HEAD, "main", "publication_incomplete")
        self.assertIn("authoritative Check result could not be published", incomplete)

    def test_known_trigger_is_compact_in_run_states_and_omitted_when_stale(self):
        for label in ("Manual request", "Draft marked ready", "PR opened for review"):
            for state in ("in_progress", "success", "action_required", "failure", "publication_incomplete"):
                with self.subTest(label=label, state=state):
                    body = check.status_comment_body(HEAD, "main", state, trigger_label=label)
                    self.assertIn(f"**Current commit:** `{HEAD[:7]}`\n**Trigger:** {label}\n", body)
                    self.assertEqual(body.count("**Trigger:**"), 1)
            stale = check.status_comment_body(OTHER, "main", "stale", trigger_label=label,
                                              last_review=(HEAD, "main", "success"))
            self.assertNotIn("**Trigger:**", stale)
            self.assertIn(f"**Current commit:** `{OTHER[:7]}` on `main` — not reviewed", stale)
            self.assertIn(f"**Last reviewed:** `{HEAD[:7]}` on `main` — ✅ clean", stale)

    def test_status_identity_round_trips_a_base_ref_with_a_slash(self):
        body = check.status_comment_body(HEAD, "release/2026", "success")
        self.assertIn("base-ref:release%2F2026", body)
        self.assertEqual(check.status_base_ref({"body": body}), "release/2026")
        stale = check.status_comment_body(OTHER, "release`candidate", "stale")
        self.assertIn("on `` release`candidate `` — not reviewed", stale)


class PRReactionTests(ScriptTestCase):
    def assert_pr_reactions_only(self):
        reaction_paths = [call["path"] for call in self.calls() if "/reactions" in call["path"]]
        self.assertTrue(all(path.startswith(f"repos/{REPO}/issues/7/reactions")
                            for path in reaction_paths), reaction_paths)

    def start(self, *, mode, existing=None, reactions=(), deletes=(), post=False):
        rules = [
            {"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
            status_list_rule(ok(pages([existing] if existing else []))),
            status_patch_rule(ok("{}")),
            status_create_rule(ok('{"id":55}')),
            reaction_list_rule(ok(pages(list(reactions)))),
            *(reaction_delete_rule(item, ok("")) for item in deletes),
        ]
        if post:
            rules.append(reaction_post_rule(ok('{"id":80}')))
        return self.run_script(
            ["create"], rules, STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE=mode,
        )

    def test_automatic_first_review_and_same_head_rerun_project_eyes(self):
        result = self.start(mode="automatic", post=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls("POST")[-1]["body"], {"content": "eyes"})
        self.assert_pr_reactions_only()
        self.assertIn("🔄 Claude Review in progress", self.calls("POST")[-2]["body"]["body"])

        self.log.unlink()
        result = self.start(
            mode="automatic", existing=status_comment(),
            reactions=[reaction(70, "+1"), reaction(71, "eyes", login="example-user", user_type="User")],
            deletes=[70], post=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assertEqual(self.calls("POST")[-1]["body"], {"content": "eyes"})
        self.assert_pr_reactions_only()

    def test_manual_same_head_rerun_moves_clean_to_pr_progress_and_back(self):
        # Only the reserved github-actions[bot] PR pair belongs to Claude Review.
        # Other actors' PR reactions survive; the native invocation comment is untouched.
        result = self.start(
            mode="manual", existing=status_comment(),
            reactions=[reaction(70, "+1"), reaction(71, "eyes", login="claude[bot]"),
                       reaction(72, "+1", login="example-user", user_type="User"),
                       reaction(73, "eyes", login="chatgpt-codex-connector[bot]")],
            deletes=[70], post=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assertEqual(self.calls("POST")[-1]["body"], {"content": "eyes"})
        self.assertIn("🔄 Claude Review in progress", self.calls("PATCH")[0]["body"]["body"])
        self.assert_pr_reactions_only()

        self.log.unlink()
        self.before.write_text("[]")
        active = {**status_comment(), "body": check.status_comment_body(HEAD, "main", "in_progress")}
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([active]))), status_patch_rule(ok("{}")),
             reaction_list_rule(ok(pages([reaction(80, "eyes"),
                                          reaction(71, "eyes", login="claude[bot]"),
                                          reaction(72, "+1", login="example-user", user_type="User"),
                                          reaction(73, "eyes", login="chatgpt-codex-connector[bot]")]))),
             reaction_delete_rule(80, ok("")), reaction_post_rule(ok('{"id":81}'))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="manual",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], "success")
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/80"])
        self.assertEqual(self.calls("POST")[0]["body"], {"content": "+1"})
        self.assert_pr_reactions_only()

    def test_clean_completion_replaces_only_owned_eyes_with_thumbs_up(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([{**status_comment(), "body": check.status_comment_body(HEAD, "main", "in_progress") }]))),
             status_patch_rule(ok("{}")),
             reaction_list_rule(ok(pages([
                 reaction(70, "eyes"), reaction(71, "eyes", login="claude[bot]", user_type="Bot"),
                 reaction(72, "+1", login="example-user", user_type="User"),
             ]))), reaction_delete_rule(70, ok("")), reaction_post_rule(ok('{"id":80}'))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="manual",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], "success")
        self.assertIn("✅ Claude Review passed", self.calls("PATCH")[-1]["body"]["body"])
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assertEqual(self.calls("POST")[0]["body"], {"content": "+1"})
        self.assert_pr_reactions_only()

    def test_automatic_clean_completion_removes_live_user_typed_eyes_before_thumbs_up(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([status_comment()]))), status_patch_rule(ok("{}")),
             reaction_list_rule(ok(pages([reaction(70, "eyes")]))),
             reaction_delete_rule(70, ok("")), reaction_post_rule(ok('{"id":80}'))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
            PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], "success")
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assertEqual(self.calls("POST")[0]["body"], {"content": "+1"})
        self.assert_pr_reactions_only()

    def test_clean_rerun_removes_eyes_without_duplicating_existing_thumbs_up(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([status_comment()]))), status_patch_rule(ok("{}")),
             reaction_list_rule(ok(pages([reaction(70, "eyes"), reaction(71, "+1"),
                                          reaction(72, "+1", login="chatgpt-codex-connector[bot]")]))),
             reaction_delete_rule(70, ok(""))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
            PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assertEqual(self.calls("POST"), [])
        self.assert_pr_reactions_only()

    def test_clean_projection_keeps_one_owned_thumbs_up_and_removes_duplicate(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([status_comment()]))), status_patch_rule(ok("{}")),
             reaction_list_rule(ok(pages([reaction(70, "eyes"), reaction(71, "+1"),
                                          reaction(72, "+1"),
                                          reaction(73, "+1", login="example-user")]))),
             reaction_delete_rule(70, ok("")), reaction_delete_rule(72, ok(""))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
            PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70",
                          f"repos/{REPO}/issues/7/reactions/72"])
        self.assertEqual(self.calls("POST"), [])
        self.assert_pr_reactions_only()

    def test_active_projection_keeps_one_owned_eyes_and_removes_duplicate(self):
        result = self.start(
            mode="automatic", existing=status_comment(),
            reactions=[reaction(70, "eyes"), reaction(71, "eyes"),
                       reaction(72, "eyes", login="claude[bot]", user_type="Bot"),
                       reaction(73, "+1", login="chatgpt-codex-connector[bot]")],
            deletes=[71],
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/71"])
        self.assertEqual(len(self.calls("POST")), 1)  # Check only; no second eyes.
        self.assert_pr_reactions_only()

    def test_stale_keeps_last_clean_result_and_clears_only_owned_reactions(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(OTHER)), status_list_rule(ok(pages([status_comment()]))),
             status_patch_rule(ok("{}")), reaction_list_rule(ok(pages([
                 reaction(70, "+1"), reaction(71, "+1", login="example-user", user_type="User"),
             ]))), reaction_delete_rule(70, ok(""))],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="stale",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        body = self.calls("PATCH")[0]["body"]["body"]
        self.assertIn("⚠️ Claude Review stale", body)
        self.assertIn(f"**Current commit:** `{OTHER[:7]}` on `main` — not reviewed", body)
        self.assertIn(f"**Last reviewed:** `{HEAD[:7]}` on `main` — ✅ clean", body)
        self.assertEqual(check.last_completed_review({"body": body}), (HEAD, "main", "success"))
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assert_pr_reactions_only()

    def test_base_retarget_and_second_stale_head_keep_the_last_completed_review(self):
        prior = status_comment(HEAD, base_ref="main")
        for current_head, current_base in ((HEAD, "release"), (OTHER, "release")):
            with self.subTest(head=current_head, base=current_base):
                self.log.unlink(missing_ok=True)
                result = self.run_script(
                    ["stale"],
                    [pr_head_rule(live_head(current_head, current_base)),
                     status_list_rule(ok(pages([prior]))), status_patch_rule(ok("{}")),
                     reaction_list_rule(ok(pages([])))],
                    HEAD_SHA=current_head, BASE_REF=current_base,
                    STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="stale",
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                body = self.calls("PATCH")[0]["body"]["body"]
                self.assertEqual(check.last_completed_review({"body": body}), (HEAD, "main", "success"))
                self.assertIn(f"**Current commit:** `{current_head[:7]}` on `{current_base}` — not reviewed", body)
                prior = {**prior, "body": body}

    def test_findings_and_failure_remove_workflow_pr_reactions(self):
        for inline, review_result, expected in (([comment(9)], "success", "action_required"),
                                                ([], "failure", "failure")):
            with self.subTest(expected=expected):
                self.log.unlink(missing_ok=True)
                self.before.write_text("[]")
                result = self.run_script(
                    ["finalize"],
                    [history_rule(current_run_only()), comments_rule(ok(pages(inline))), patch_rule(ok("{}")),
                     status_list_rule(ok(pages([status_comment()]))), status_patch_rule(ok("{}")),
                     reaction_list_rule(ok(pages([reaction(70, "+1"), reaction(74, "eyes")]))),
                     reaction_delete_rule(70, ok("")), reaction_delete_rule(74, ok(""))],
                    CHECK_RUN_ID="99", REVIEW_RESULT=review_result, ACTION_CONCLUSION="success",
                    BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
                    PR_REACTION_MODE="manual",
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], expected)
                self.assertEqual([call["path"] for call in self.calls("DELETE")],
                                 [f"repos/{REPO}/issues/7/reactions/70",
                                  f"repos/{REPO}/issues/7/reactions/74"])
                self.assertEqual(self.calls("POST"), [])

    def test_reaction_delete_failure_cannot_change_clean_check_or_add_mixed_state(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([status_comment()]))), status_patch_rule(ok("{}")),
             reaction_list_rule(ok(pages([reaction(70, "eyes")]))), reaction_delete_rule(70, FAIL),
             reaction_post_rule(ok('{"id":80}'))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
            PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], "success")
        self.assertEqual(self.calls("POST"), [])
        self.assertIn("Could not remove an obsolete", result.stdout)

    def test_status_update_failure_clears_obsolete_owned_reaction_without_changing_check(self):
        self.before.write_text("[]")
        result = self.run_script(
            ["finalize"],
            [history_rule(current_run_only()), comments_rule(ok(pages([]))), patch_rule(ok("{}")),
             status_list_rule(ok(pages([status_comment()]))), status_patch_rule(FAIL),
             reaction_list_rule(ok(pages([reaction(70, "eyes"),
                                          reaction(71, "+1", login="example-user", user_type="User")]))),
             reaction_delete_rule(70, ok(""))],
            CHECK_RUN_ID="99", REVIEW_RESULT="success", ACTION_CONCLUSION="success",
            BEFORE_IDS_FILE=str(self.before), STATUS_COMMENTS_ENABLED="true",
            PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls("PATCH")[0]["body"]["conclusion"], "success")
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assertEqual(self.calls("POST"), [])

    def test_fork_without_check_never_projects_clean_completion(self):
        result = self.run_script(
            ["create"],
            [{"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [FAIL]},
             status_list_rule(ok(pages([]))), status_create_rule(ok('{"id":55}')),
             reaction_list_rule(ok(pages([reaction(70, "+1")]))),
             reaction_delete_rule(70, ok("")), reaction_post_rule(ok('{"id":80}'))],
            IS_FORK="true", STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0)
        comment_body = next(call["body"]["body"] for call in self.calls("POST")
                            if call["path"] == f"repos/{REPO}/issues/7/comments")
        self.assertIn("Check publication is unavailable", comment_body)
        self.assertEqual(self.calls("POST")[-1]["path"], f"repos/{REPO}/issues/7/comments")
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assert_pr_reactions_only()

    def test_repeated_projection_is_idempotent_and_failure_is_best_effort(self):
        result = self.start(
            mode="automatic", existing=status_comment(), reactions=[reaction(70, "eyes")],
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.calls("DELETE"), [])
        self.assertEqual(len(self.calls("POST")), 1)  # Check only.

        self.log.unlink()
        result = self.run_script(
            ["create"],
            [{"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
             status_list_rule(ok(pages([status_comment()]))), status_patch_rule(ok("{}")),
             reaction_list_rule(FAIL)],
            STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Could not reconcile Claude Review reactions", result.stdout)
        self.assertEqual(len(self.calls("PATCH")), 1)

    def test_reaction_post_failure_does_not_fail_the_review_start(self):
        result = self.run_script(
            ["create"],
            [{"method": "POST", "path": f"repos/{REPO}/check-runs", "responses": [ok('{"id":99}')]},
             status_list_rule(ok(pages([]))), status_create_rule(ok('{"id":55}')),
             reaction_list_rule(ok(pages([]))), reaction_post_rule(FAIL)],
            STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="automatic",
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("check_run_id=99", self.output.read_text())
        self.assertEqual(len(self.calls("POST")), 3)
        self.assertIn("Could not reconcile Claude Review reactions", result.stdout)

    def test_same_identity_stale_event_does_not_clear_active_review(self):
        active = {**status_comment(), "body": check.status_comment_body(HEAD, "main", "in_progress")}
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(HEAD)), status_list_rule(ok(pages([active]))),
             reaction_list_rule(ok(pages([reaction(70, "eyes")]))), reaction_delete_rule(70, ok(""))],
            STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="stale",
        )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(any("reactions" in call["path"] for call in self.calls()))

    def test_stale_findings_presentation_keeps_the_reviewed_result(self):
        prior = {**status_comment(), "body": check.status_comment_body(HEAD, "main", "action_required")}
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(OTHER)), status_list_rule(ok(pages([prior]))),
             status_patch_rule(ok("{}")), reaction_list_rule(ok(pages([])))],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="stale",
        )
        self.assertEqual(result.returncode, 0)
        body = self.calls("PATCH")[0]["body"]["body"]
        self.assertIn(f"**Last reviewed:** `{HEAD[:7]}` on `main` — ⚠️ findings", body)
        self.assertEqual(check.last_completed_review({"body": body}), (HEAD, "main", "action_required"))

    def test_stale_clears_pr_reactions_even_when_the_status_comment_is_missing(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(OTHER)), status_list_rule(ok(pages([]))),
             reaction_list_rule(ok(pages([reaction(70, "+1"),
                                          reaction(71, "eyes", login="claude[bot]")]))),
             reaction_delete_rule(70, ok(""))],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="stale",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls("PATCH") + self.calls("POST"), [])
        self.assertEqual([call["path"] for call in self.calls("DELETE")],
                         [f"repos/{REPO}/issues/7/reactions/70"])
        self.assert_pr_reactions_only()

    def test_stale_does_not_clear_newer_head_reactions_during_refresh(self):
        result = self.run_script(
            ["stale"],
            [pr_head_rule(live_head(OTHER), live_head(NEWER)),
             status_list_rule(ok(pages([]))),
             reaction_list_rule(ok(pages([reaction(70, "+1")]))),
             reaction_delete_rule(70, ok(""))],
            HEAD_SHA=OTHER, STATUS_COMMENTS_ENABLED="true", PR_REACTION_MODE="stale",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls("PATCH"), [])
        self.assertFalse(any("/reactions" in call["path"] for call in self.calls()))

    def test_stale_without_a_completed_review_does_not_imply_one_exists(self):
        body = check.status_comment_body(OTHER, "main", "stale")
        self.assertIn("not covered by a completed Claude Review", body)
        self.assertNotIn("**Last reviewed:**", body)

    def test_legacy_terminal_result_is_preserved_on_first_stale_transition(self):
        legacy = status_comment()
        legacy["body"] = (
            f"{check.STATUS_MARKER}\n{check.STATUS_HEAD_PREFIX}{HEAD} -->\n"
            f"{check.STATUS_BASE_REF_PREFIX}main -->\n"
            "Claude Review completed with no findings for this commit."
        )
        self.assertEqual(check.last_completed_review(legacy), (HEAD, "main", "success"))
        for state, result in (("success", "success"), ("action_required", "action_required")):
            with self.subTest(state=state):
                rendered = check.status_comment_body(HEAD, "main", state)
                self.assertEqual(check.last_completed_review({"body": rendered})[2], result)
                self.assertIn(f"**Current commit:** `{HEAD[:7]}`", rendered)
                self.assertIn(HEAD, rendered)  # Full SHA remains in hidden identity.


if __name__ == "__main__":
    unittest.main()
