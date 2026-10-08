#!/usr/bin/env python3
"""Publish the trusted `Claude Review` Check Run for one reviewed PR snapshot.

Runs only in workflow jobs that never invoke Claude. The check run is the
authoritative per-commit review state; the model never writes it.

Subcommands (configured through environment variables set by the workflow):
  create    open an in_progress check run for HEAD_SHA
  snapshot  record Claude's review-comment IDs on HEAD_SHA before the review
  finalize  map the review outcome to a conclusion and complete the check run
  stale     refresh an existing status comment for a new PR head
  admit-manual  reject a manual snapshot whose PR base projection is outdated
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlencode

CHECK_NAME = "Claude Review"
CLAUDE_ACTOR_LOGIN = "claude[bot]"
CHECK_APP_SLUG = "github-actions"
STATUS_MARKER = "<!-- claude-review-runtime:claude-review-status -->"
STATUS_HEAD_PREFIX = "<!-- claude-review-runtime:claude-review-status-head:"
STATUS_BASE_REF_PREFIX = "<!-- claude-review-runtime:claude-review-status-base-ref:"
STATUS_STATE_PREFIX = "<!-- claude-review-runtime:claude-review-status-state:"
STATUS_REVIEWED_HEAD_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-head:"
STATUS_REVIEWED_BASE_REF_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-base-ref:"
STATUS_REVIEWED_RESULT_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-result:"
STATUS_REVIEWED_BASE_SHA_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-base-sha:"
STATUS_REVIEWED_GENERATION_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-generation:"
STATUS_REVIEWED_COMPLETED_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-completed-at:"
STATUS_REVIEWED_RUN_URL_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-run-url:"
STATUS_AUTHOR = "github-actions[bot]"
OWNER_PHASE_PREFIX = "<!-- claude-review-runtime:presentation-owner-phase:"
OWNER_PREFIX = "<!-- claude-review-runtime:presentation-owner:"
STATUS_REASONS = {
    "cancelled": "The review was cancelled before verified completion.",
    "preparation_failed": "Review preparation failed; Claude did not complete this review.",
    "incomplete": "The review did not complete with reliable execution and finding evidence.",
    "check_publication_failed": "The authoritative Check result could not be published.",
    "tooling_unavailable": "Trusted review tooling was unavailable; the review could not be finalized reliably.",
    "stale": "The captured head or base was superseded; the current patch is not reviewed by this attempt.",
    "base_advanced": "The target branch advanced; integration with the current baseline has not been reviewed.",
    "completion_metadata_unverified": "The stored presentation metadata cannot establish a completed result for this attempt.",
}
PRESENTATION_SECONDS = 120
PRESENTATION_DEADLINE = None
COMPLETION_PREFIX = "<!-- claude-review-runtime:manual-clean-completion:"
ATTEMPTS = 3
GH_TIMEOUT_SECONDS = 30
RETRY_DELAY_SECONDS = 2
# Check Run output.summary/output.text have a 65,535-byte limit. Keep generous
# headroom, including for non-ASCII diagnostics (https://docs.github.com/rest/checks/runs).
EVIDENCE_ID_LIMIT = 64
# Complete publication evidence is validated before diagnostic sampling.
PUBLICATION_RECEIPT_LIMIT = 64
MAX_COMMENT_ID = 9007199254740991
CHECK_SUMMARY_BYTES = 8192
EVIDENCE_STRING_BYTES = 2048
EVIDENCE_ERROR_BYTES = 1024
EVIDENCE_ID_FIELDS = (
    "new_finding_comment_ids", "claude_finding_comment_ids", "observed_head_comment_ids",
    "unscoped_finding_comment_ids", "other_diff_finding_comment_ids",
    "prior_finding_check_run_ids",
)
# Authoritative Check/evidence and completion-notice worst cases as
# (GitHub calls, retried operations). Presentation uses its separate elapsed
# PRESENTATION_SECONDS budget, including pagination and readback. Manual
# admission and completion notices each have an independent elapsed budget
# of the same duration, reset before any authoritative Check work. Each call is
# capped at GH_TIMEOUT_SECONDS and each retried operation adds its backoff.
# create's retries each add a reconciliation lookup before POSTing again;
# finalize may look up an unreported run, then read history and comments and
# PATCH the result and its fallback. The tests check the implementation against
# this model and the start-check and publish-status job timeouts against the
# budget it implies, so publication is not cut off by a job timeout.
WORST_CASE = {
    "create": (1 + 2 * (ATTEMPTS - 1), 1),
    "snapshot": (ATTEMPTS, 1),
    "finalize": (5 * ATTEMPTS, 5),
    # Paginated dedup discovery and the live snapshot read may retry; the
    # completion POST is attempted once because a lost response is ambiguous.
    "completion": (4 * ATTEMPTS + 1, 4),
    "admit-manual": (3 * ATTEMPTS, 3),
}


class GhError(Exception):
    pass


def gh_api(args, body=None):
    timeout = GH_TIMEOUT_SECONDS
    if PRESENTATION_DEADLINE is not None:
        timeout = min(timeout, PRESENTATION_DEADLINE - time.monotonic())
        if timeout <= 0:
            raise GhError("presentation budget exhausted")
    try:
        proc = subprocess.run(
            ["gh", "api", *args],
            input=None if body is None else json.dumps(body),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise GhError(f"gh api {args[0]} timed out") from error
    if proc.returncode != 0:
        raise GhError(proc.stderr.strip() or f"gh api exited {proc.returncode}")
    return proc.stdout


def with_retries(action, description):
    delay = float(os.environ.get("CLAUDE_REVIEW_RETRY_DELAY", RETRY_DELAY_SECONDS))
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return action()
        except GhError as error:
            print(f"::warning::{description} failed (attempt {attempt}/{ATTEMPTS}): {error}")
            if attempt == ATTEMPTS:
                raise
            pause = delay * 2 ** (attempt - 1)
            if PRESENTATION_DEADLINE is not None:
                pause = min(pause, max(0, PRESENTATION_DEADLINE - time.monotonic()))
            time.sleep(pause)


def parse_pages(text, *, object_pairs_hook=None):
    """Decode `gh api --paginate` output: one JSON document per page, concatenated."""
    decoder = json.JSONDecoder(object_pairs_hook=object_pairs_hook)
    pages, index = [], 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return pages
        page, index = decoder.raw_decode(text, index)
        pages.append(page)


def positive_comment_id(value):
    return type(value) is int and 0 < value <= MAX_COMMENT_ID


def valid_comment_id_list(value):
    return (isinstance(value, list) and all(positive_comment_id(item) for item in value)
            and len(set(value)) == len(value))


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def publication_receipts(raw):
    """Validate complete cross-job evidence; absent data never means no attempts."""
    if len(raw) > 2048:
        raise ValueError("publication receipt evidence exceeds its bound")
    value = json.loads(raw, object_pairs_hook=unique_json_object)
    if not isinstance(value, dict) or set(value) != {"attempt_count", "comment_ids"}:
        raise ValueError("publication receipt evidence needs an exact count and ID list")
    count, ids = value["attempt_count"], value["comment_ids"]
    if (type(count) is not int or not 0 <= count <= PUBLICATION_RECEIPT_LIMIT
            or not valid_comment_id_list(ids) or len(ids) != count):
        raise ValueError("publication receipt evidence has an invalid count or ID list")
    return value


def claude_comment_ids(pages, head_sha):
    # original_commit_id is the commit_id supplied when the comment was created;
    # commit_id itself can move to a later head as the PR advances.
    return sorted(
        comment["id"]
        for page in pages
        for comment in page
        if comment.get("user", {}).get("login") == CLAUDE_ACTOR_LOGIN
        and comment.get("user", {}).get("type") == "Bot"
        and comment.get("original_commit_id") == head_sha
    )


def recorded_evidence(run):
    """The evidence object a completed run recorded in its output text, or {}."""
    text = (run.get("output") or {}).get("text") or ""
    start, end = text.find("```json\n"), text.rfind("\n```")
    if start == -1 or end <= start:
        return {}
    try:
        evidence = json.loads(text[start + len("```json\n") : end])
    except ValueError:
        return {}
    return evidence if isinstance(evidence, dict) else {}


def recorded_finding_ids(run):
    """Finding comment IDs a completed run recorded in its evidence block, if any."""
    evidence = recorded_evidence(run)
    try:
        return sorted(
            set(evidence.get("new_finding_comment_ids") or [])
            | set(evidence.get("claude_finding_comment_ids") or [])
        )
    except TypeError:
        return []


def prior_finding_evidence(pages, head_sha, current_check_run_id, pr_number, merge_base_sha):
    """Classify trusted finding evidence by the reviewed PR/head/merge-base.

    A raw review comment names its original head but not the diff's merge
    base. Only completed trusted Check evidence can attribute earlier comment
    IDs to this diff or to another diff of the same PR/head. Failed runs also
    preserve any findings they recorded. Keep recorded IDs even when the
    corresponding comments have since been deleted.
    """
    if not pr_number or not merge_base_sha:
        return [], set(), set()
    matching_runs, matching_ids, other_diff_ids = [], set(), set()
    for page in pages:
        for run in page.get("check_runs", []):
            evidence = recorded_evidence(run)
            if not (
                run.get("name") == CHECK_NAME
                and (run.get("app") or {}).get("slug") == CHECK_APP_SLUG
                and run.get("head_sha") == head_sha
                and str(run.get("id")) != str(current_check_run_id)
                and run.get("status") == "completed"
                and str(evidence.get("pr_number")) == str(pr_number)
                and evidence.get("merge_base_sha")
            ):
                continue
            finding_ids = recorded_finding_ids(run)
            if evidence["merge_base_sha"] == merge_base_sha:
                matching_ids.update(finding_ids)
                if (run.get("conclusion") == "action_required" or finding_ids
                        or evidence.get("same_diff_findings_recorded") is True):
                    matching_runs.append(run["id"])
            else:
                other_diff_ids.update(finding_ids)
    return sorted(matching_runs), matching_ids, other_diff_ids - matching_ids


def fetch_claude_comment_ids(repo, pr_number, head_sha):
    path = f"repos/{repo}/pulls/{pr_number}/comments?per_page=100"
    text = with_retries(lambda: gh_api(["--paginate", path]), "Listing PR review comments")
    try:
        pages = parse_pages(text)
        if not pages or any(not isinstance(page, list) for page in pages):
            raise ValueError("expected complete comment pages")
        for page in pages:
            if any(not isinstance(item, dict) or not positive_comment_id(item.get("id"))
                   or not isinstance(item.get("user"), dict) for item in page):
                raise ValueError("malformed comment evidence")
        return claude_comment_ids(pages, head_sha)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise GhError("PR review comment evidence is malformed or incomplete") from error


def check_run_history(repo, head_sha, *, required_check_run_id=None):
    # filter=all: the endpoint's default returns only the latest run per name.
    query = urlencode({"check_name": CHECK_NAME, "filter": "all", "per_page": 100})
    raw = gh_api(["--paginate", f"repos/{repo}/commits/{head_sha}/check-runs?{query}"])
    try:
        pages = parse_pages(raw, object_pairs_hook=unique_json_object)
        # An empty body or missing collection cannot establish absence. Check
        # all pages before consuming any history, including recovery lookups.
        if not pages or any(not isinstance(page, dict)
                            or not isinstance(page.get("check_runs"), list)
                            or type(page.get("total_count")) is not int
                            or page["total_count"] < 0 for page in pages):
            raise ValueError("expected complete check history pages")
        total, runs = pages[0]["total_count"], {}
        for page in pages:
            # Each page reports the aggregate count. A moving listing may
            # change totals or repeat IDs; retry the entire read in that case.
            if page["total_count"] != total:
                raise ValueError("check history total changed during pagination")
            for run in page["check_runs"]:
                if (not isinstance(run, dict) or not positive_comment_id(run.get("id"))
                        or any(not isinstance(run.get(key), str) for key in ("name", "head_sha", "status"))
                        # Null/missing app or slug cannot identify our trusted
                        # publisher; the classifier safely skips those runs.
                        or (run.get("app") is not None and not isinstance(run["app"], dict))
                        or (isinstance(run.get("app"), dict)
                            and run["app"].get("slug") is not None
                            and not isinstance(run["app"]["slug"], str))
                        or (run.get("conclusion") is not None and not isinstance(run["conclusion"], str))
                        or (run.get("external_id") is not None and not isinstance(run["external_id"], str))
                        or (run.get("output") is not None and not isinstance(run["output"], dict))
                        or (isinstance(run.get("output"), dict)
                            and run["output"].get("text") is not None
                            and not isinstance(run["output"]["text"], str))):
                    raise ValueError("malformed check run")
                if run["id"] in runs:
                    raise ValueError("duplicate check run in history")
                runs[run["id"]] = run
        if len(runs) != total:
            raise ValueError("check history does not match its total")
        if required_check_run_id is not None:
            current = next((run for run in runs.values()
                            if str(run["id"]) == str(required_check_run_id)), None)
            if (current is None or current["name"] != CHECK_NAME
                    or current["head_sha"] != head_sha
                    or (current.get("app") or {}).get("slug") != CHECK_APP_SLUG):
                raise ValueError("captured check run is absent from history")
        return pages
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise GhError("Claude Review check history is malformed or incomplete") from error


def fetch_prior_finding_evidence(repo, head_sha, current_check_run_id, pr_number, merge_base_sha):
    history = with_retries(
        lambda: check_run_history(repo, head_sha, required_check_run_id=current_check_run_id),
        "Listing Claude Review check history"
    )
    return history, prior_finding_evidence(history, head_sha, current_check_run_id, pr_number, merge_base_sha)


def completed_check_timestamp(history, check_run_id, head_sha, conclusion):
    """Retain a dated completion of this captured Check across finalization retries."""
    if conclusion not in ("success", "action_required"):
        return None
    for page in history:
        for run in page.get("check_runs", []):
            evidence = recorded_evidence(run)
            if (str(run.get("id")) == str(check_run_id)
                    and run.get("name") == CHECK_NAME
                    and (run.get("app") or {}).get("slug") == CHECK_APP_SLUG
                    and run.get("head_sha") == head_sha
                    and run.get("status") == "completed"
                    and run.get("conclusion") == conclusion
                    and evidence.get("completion_verified") is True
                    and all(key in evidence and evidence[key] == value
                            for key, value in review_identity(head_sha).items())):
                return valid_review_timestamp(run.get("completed_at"))
    return None


def find_check_run_by_external_id(repo, head_sha, external_id):
    if not external_id:
        return None
    for page in check_run_history(repo, head_sha):
        for run in page.get("check_runs", []):
            if run.get("external_id") == external_id:
                return run["id"]
    return None


def decide(
    review_result,
    action_conclusion,
    new_ids,
    earlier_ids,
    prior_ids,
    evidence_error=None,
    start_result="success",
    completion_verified=False,
    completion_reason="",
):
    """Return (conclusion, title, result sentence) for the reviewed commit.

    new_ids are Claude findings this run posted; earlier_ids are findings
    recorded for this PR/head/merge-base; prior_ids are earlier check runs
    that recorded findings for that identity. Any keeps a completed review
    action_required.
    Only an explicitly verified completion can produce success.
    """
    notes = ""
    if earlier_ids:
        notes += f" {len(earlier_ids)} earlier Claude finding(s) are recorded on this commit."
    if prior_ids:
        notes += (
            f" An earlier Claude Review of this exact commit recorded findings "
            f"(check run {', '.join(map(str, prior_ids))})."
        )
    kept = ""
    if new_ids:
        kept = (
            f" Claude posted {len(new_ids)} inline finding(s) in this run;"
            " they stay recorded for this commit."
        )
    if start_result != "success":
        return (
            "failure",
            "Review did not start",
            f"Preparing the review failed (start-check result `{start_result or 'unknown'}`), "
            f"so Claude did not review this commit in this run.{notes}",
        )
    if review_result != "success":
        return (
            "failure",
            "Review did not complete",
            f"The review job did not succeed (result `{review_result or 'unknown'}`), "
            f"so this commit has no completed Claude review from this run.{kept}{notes}",
        )
    if action_conclusion != "success":
        return (
            "failure",
            "Review did not complete",
            f"Claude Code reported execution conclusion `{action_conclusion or 'missing'}`.{kept}{notes}",
        )
    if not completion_verified:
        return (
            "failure",
            "Review completion could not be verified",
            "Claude Code reported success, but its execution record does not show a "
            f"completed review (`{completion_reason or 'no verdict'}`).{kept}{notes}",
        )
    if evidence_error:
        return (
            "failure",
            "Review outcome could not be determined",
            f"The review ran, but its finding evidence is incomplete: {evidence_error}.{kept}{notes}",
        )
    if new_ids:
        return (
            "action_required",
            f"{len(new_ids)} finding(s) posted",
            f"Claude posted {len(new_ids)} inline finding(s) on this commit.{notes}",
        )
    if earlier_ids or prior_ids:
        return (
            "action_required",
            "Earlier findings were recorded",
            f"This run posted no new findings.{notes}",
        )
    return ("success", "No issues found", "Claude reviewed this commit and posted no findings.")


def env(name, default=""):
    return os.environ.get(name, default)


def write_output(name, value):
    output = env("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def inline_code(value):
    ticks = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    padding = " " if "`" in value else ""
    return f"{ticks}{padding}{value}{padding}{ticks}"


def valid_review_timestamp(value):
    """Accept only canonical UTC timestamps from trusted persisted metadata."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        return None
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return value


def relative_time(value):
    return f'<relative-time datetime="{value}">{value}</relative-time>'


def owner_start_time(owner):
    # The unchanged ownership token is generated by time.time_ns() at start.
    try:
        return datetime.fromtimestamp(owner["started"] // 10**9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def status_comment_body(head_sha, base_ref, state, *, check_available=True, last_review=None,
                        last_review_details=None, base_sha=None, trigger_label=None,
                        owner=None, owner_running=None, reason=None, completed_at=None,
                        completed_owner_verified=False):
    """Render trusted informational state without affecting Check authority."""
    headings = {
        "in_progress": "🔄 Claude Review in progress",
        "success": "✅ Claude Review passed",
        "action_required": "⚠️ Claude Review found issues",
        "failure": "❌ Claude Review incomplete",
        "publication_incomplete": "❌ Claude Review incomplete",
        "stale": "⚠️ Claude Review stale",
    }
    messages = {
        "in_progress": "Claude is reviewing this commit.",
        "success": "Claude completed this review with no findings.",
        "action_required": "Claude recorded findings. Assess them in the inline review threads.",
        "failure": "This review did not complete reliably; this commit is not approved by it.",
        "publication_incomplete": "The authoritative Check result could not be published. Treat this commit as not reviewed.",
        "stale": "Current head and base coverage is not established by this attempt.",
    }
    if not check_available:
        messages["in_progress"] = "Claude is reviewing this commit, but authoritative Check publication is unavailable."
        messages["publication_incomplete"] = (
            "Review completion cannot be established because authoritative Check publication is unavailable. "
            "Consult the workflow run and inline review threads."
        )
    if state in ("success", "action_required"):
        last_review = (head_sha, base_ref, state)
        last_review_details = {"base_sha": env("BASE_SHA"), "run_url": env("DETAILS_URL"),
                               "completed_at": completed_at,
                               "generation": owner["generation"] if owner else None}
    last_review_details = last_review_details or {}
    base_advanced = (
        state == "stale" and check_available and completed_owner_verified and owner and not owner_running
        and last_review in ((head_sha, base_ref, "success"), (head_sha, base_ref, "action_required"))
        and (owner["head"], owner["base_ref"]) == (head_sha, base_ref)
        and last_review_details.get("generation") == owner["generation"]
        and last_review_details.get("base_sha") == owner["base"]
        and isinstance(base_sha, str) and re.fullmatch(r"[0-9a-f]{40}", base_sha)
        and base_sha != owner["base"]
        and reason in (STATUS_REASONS["stale"], STATUS_REASONS["base_advanced"])
    )
    if base_advanced:
        headings["stale"] = headings[last_review[2]] + " — base advanced"
        messages["stale"] = (
            "Claude reviewed this unchanged commit with no findings." if last_review[2] == "success"
            else "Claude recorded findings on this unchanged commit. Assess them in the inline review threads."
        )
        reason = STATUS_REASONS["base_advanced"]
    elif (state == "stale" and check_available and completed_owner_verified
          and last_review and last_review[:2] != (head_sha, base_ref)
          and not owner_running and reason == STATUS_REASONS["stale"]):
        headings["stale"] = (
            "Last Claude review: no findings" if last_review[2] == "success"
            else "Last Claude review: findings"
        )
        messages["stale"] = "This review doesn’t cover the current version."
    lines = [
        STATUS_MARKER,
        f"{STATUS_HEAD_PREFIX}{head_sha} -->",
        f"{STATUS_BASE_REF_PREFIX}{quote(base_ref, safe='')} -->",
        f"{STATUS_STATE_PREFIX}{state} -->",
    ]
    if owner:
        lines.append(owner_marker(owner))
        running = state == "in_progress" if owner_running is None else owner_running
        lines.append(f"{OWNER_PHASE_PREFIX}{'running' if running else 'terminal'} -->")
    if last_review:
        reviewed_sha, reviewed_base, reviewed_result = last_review
        lines += [
            f"{STATUS_REVIEWED_HEAD_PREFIX}{reviewed_sha} -->",
            f"{STATUS_REVIEWED_BASE_REF_PREFIX}{quote(reviewed_base, safe='')} -->",
            f"{STATUS_REVIEWED_RESULT_PREFIX}{reviewed_result} -->",
        ]
        reviewed_base_sha = last_review_details.get("base_sha")
        if reviewed_base_sha and re.fullmatch(r"[0-9a-f]{40}", reviewed_base_sha):
            lines.append(f"{STATUS_REVIEWED_BASE_SHA_PREFIX}{reviewed_base_sha} -->")
        if last_review_details.get("run_url"):
            lines.append(f"{STATUS_REVIEWED_RUN_URL_PREFIX}{quote(last_review_details['run_url'], safe='')} -->")
    completed = valid_review_timestamp(last_review_details.get("completed_at")) if last_review else None
    if last_review and completed:
        lines.append(f"{STATUS_REVIEWED_COMPLETED_PREFIX}{completed} -->")
    if last_review:
        generation = last_review_details.get("generation")
        if isinstance(generation, str) and re.fullmatch(r"[0-9a-f]{64}", generation):
            lines.append(f"{STATUS_REVIEWED_GENERATION_PREFIX}{generation} -->")
    lines += [f"### {headings[state]}", ""]
    # Failure and running receipts remain ahead of historical completion.
    # Keep the fixed Reason line visible and parseable for terminal recovery.
    if reason and reason not in (STATUS_REASONS["stale"], STATUS_REASONS["base_advanced"]):
        lines += [f"**Reason:** {reason}", ""]
    if state == "stale" and owner_running:
        lines += ["🔄 The latest review attempt is still in progress.", ""]
    if state == "stale" and last_review:
        reviewed_sha, reviewed_base, reviewed_result = last_review
        result = "✅ clean" if reviewed_result == "success" else "⚠️ findings"
        lines.append(f"**Last reviewed:** `{reviewed_sha[:7]}` on {inline_code(reviewed_base)} — {result}")
    if state in ("success", "action_required"):
        lines.append(f"**Reviewed commit:** `{head_sha[:7]}`")
    if completed and state in ("success", "action_required", "stale"):
        label = "Last review completed" if state == "stale" else "Completed"
        lines.append(f"**{label}:** {relative_time(completed)}")
    lines += ["", messages[state]]
    if base_advanced:
        lines += ["", "Integration with the current baseline has not been reviewed."]
    if state == "stale" and last_review and last_review[2] == "action_required":
        lines += ["", "Recorded findings remain in the inline review threads."]
    lines += ["", "<details>", "<summary>Review details</summary>", ""]
    lines += [
        f"**Current commit:** `{head_sha[:7]}`" +
        (f" on {inline_code(base_ref)} — " +
         ("reviewed clean" if last_review[2] == "success" else "reviewed with findings")
         if base_advanced else f" on {inline_code(base_ref)} — not reviewed" if state == "stale" else ""),
    ]
    if state != "stale" and trigger_label:
        lines.append(f"**Trigger:** {trigger_label}")
    if owner and (started := owner_start_time(owner)):
        lines.append(f"**Started:** {relative_time(started)}")
    if owner:
        if state == "stale":
            lines.append(f"**Captured commit:** `{owner['head'][:7]}`")
        lines.append(f"**Captured baseline:** `{owner['base'][:7]}` on {inline_code(owner['base_ref'])}")
    if state == "stale" and base_sha:
        lines.append(f"**Current baseline:** `{base_sha[:7]}` — " +
                     ("integration not reviewed" if base_advanced else "not reviewed"))
    if state == "stale" and last_review_details.get("base_sha"):
        lines.append(f"**Reviewed baseline:** `{last_review_details['base_sha'][:7]}`")
    if reason in (STATUS_REASONS["stale"], STATUS_REASONS["base_advanced"]):
        lines += ["", f"**Reason:** {reason}"]
    lines += ["", "</details>"]
    attempt_url = (env("DETAILS_URL") if not owner else
                   f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{owner['repo']}/actions/runs/{owner['run']}")
    historical = state == "stale" and last_review is not None
    if owner and state == "stale" and (not historical or last_review_details.get("run_url") != attempt_url):
        lines += ["", f"[Attempt workflow run]({attempt_url})"]
    run_url = last_review_details.get("run_url") if historical else attempt_url
    if run_url and (historical or not (owner and state == "stale")):
        label = "Reviewed workflow run" if historical else "Workflow run"
        lines += ["", f"[{label}]({run_url})"]
    return "\n".join(lines)


def find_status_comment(repo, pr_number):
    path = f"repos/{repo}/issues/{pr_number}/comments?per_page=100"
    raw = with_retries(lambda: gh_api(["--paginate", path]), "Listing PR conversation comments")
    owned = [
        comment
        for page in parse_pages(raw)
        for comment in page
        if (comment.get("user") or {}).get("login") == STATUS_AUTHOR
        and (comment.get("user") or {}).get("type") == "Bot"
        and STATUS_MARKER in (comment.get("body") or "")
    ]
    return min(owned, key=lambda comment: comment["id"]) if owned else None


def status_head(comment):
    match = re.search(re.escape(STATUS_HEAD_PREFIX) + r"([0-9a-f]{40}) -->", comment.get("body") or "")
    return match.group(1) if match else None


def status_base_ref(comment):
    match = re.search(
        re.escape(STATUS_BASE_REF_PREFIX) + r"([A-Za-z0-9_.~%\-]+) -->",
        comment.get("body") or "",
    )
    return unquote(match.group(1)) if match else None


def status_state(comment):
    match = re.search(
        re.escape(STATUS_STATE_PREFIX) +
        r"(in_progress|success|action_required|failure|publication_incomplete|stale) -->",
        comment.get("body") or "",
    )
    return match.group(1) if match else None


def last_completed_review(comment):
    """Read the stored result, including a terminal result in a legacy comment."""
    body = comment.get("body") or ""
    head = re.search(re.escape(STATUS_REVIEWED_HEAD_PREFIX) + r"([0-9a-f]{40}) -->", body)
    base = re.search(re.escape(STATUS_REVIEWED_BASE_REF_PREFIX) + r"([A-Za-z0-9_.~%\-]+) -->", body)
    result = re.search(re.escape(STATUS_REVIEWED_RESULT_PREFIX) + r"(success|action_required) -->", body)
    if head and base and result:
        return head.group(1), unquote(base.group(1)), result.group(1)
    # Existing deployed comments have only head/base markers and fixed prose.
    if "Claude Review completed with no findings for this commit." in body:
        legacy_result = "success"
    elif "Claude Review recorded findings for this commit." in body:
        legacy_result = "action_required"
    else:
        return None
    legacy_head, legacy_base = status_head(comment), status_base_ref(comment)
    return (legacy_head, legacy_base, legacy_result) if legacy_head and legacy_base else None


def last_completed_review_details(comment):
    """Read optional historical metadata without substituting the current owner's patch/run."""
    body = (comment or {}).get("body") or ""
    details = {}
    for key, prefix, pattern in (
            ("base_sha", STATUS_REVIEWED_BASE_SHA_PREFIX, r"([0-9a-f]{40}) -->"),
            ("run_url", STATUS_REVIEWED_RUN_URL_PREFIX, r"([A-Za-z0-9_.~%\-]+) -->")):
        match = re.search(re.escape(prefix) + pattern, body)
        if match:
            details[key] = unquote(match.group(1))
    values = re.findall(re.escape(STATUS_REVIEWED_COMPLETED_PREFIX) + r"(.*?) -->", body)
    if len(values) == 1 and valid_review_timestamp(values[0]):
        details["completed_at"] = values[0]
    generations = re.findall(re.escape(STATUS_REVIEWED_GENERATION_PREFIX) + r"(.*?) -->", body)
    if len(generations) == 1 and re.fullmatch(r"[0-9a-f]{64}", generations[0]):
        details["generation"] = generations[0]
    if not values and not generations and owned_status(comment or {}):
        # A legacy terminal result proves which owner completed, even without a
        # completion date. Carry that provenance through later stale refreshes.
        owner = status_owner(comment)
        state = status_state(comment)
        if (owner and state in ("success", "action_required")
                and not status_owner_running(comment)
                and last_completed_review(comment) == (owner["head"], owner["base_ref"], state)):
            details["generation"] = owner["generation"]
    return details


def completed_review_for_owner(comment, owner):
    """Require unambiguous trusted completion provenance before positive presentation."""
    if not owner or not owned_status(comment or {}) or status_owner(comment) != owner:
        return False
    body = comment.get("body") or ""
    values = lambda prefix: re.findall(re.escape(prefix) + r"(.*?) -->", body)
    states = values(STATUS_STATE_PREFIX)
    reasons = re.findall(r"^\*\*Reason:\*\* (.+)$", body, re.MULTILINE)
    if states in (["success"], ["action_required"]):
        if reasons:
            return False
    elif states == ["stale"]:
        if terminal_status_reason(comment, owner) not in (
                STATUS_REASONS["stale"], STATUS_REASONS["base_advanced"]):
            return False
    else:
        return False
    review = last_completed_review(comment)
    if not review or review[:2] != (owner["head"], owner["base_ref"]):
        return False
    if states in (["success"], ["action_required"]) and states[0] != review[2]:
        return False
    expected = (
        (OWNER_PHASE_PREFIX, "terminal"),
        (STATUS_REVIEWED_HEAD_PREFIX, owner["head"]),
        (STATUS_REVIEWED_BASE_REF_PREFIX, quote(owner["base_ref"], safe="")),
        (STATUS_REVIEWED_RESULT_PREFIX, review[2]),
        (STATUS_REVIEWED_BASE_SHA_PREFIX, owner["base"]),
    )
    if any(values(prefix) != [value] for prefix, value in expected):
        return False
    generations = values(STATUS_REVIEWED_GENERATION_PREFIX)
    # Existing trusted migration can establish an undated terminal owner's generation.
    if generations and generations != [owner["generation"]]:
        return False
    if last_completed_review_details(comment).get("generation") != owner["generation"]:
        return False
    timestamps = values(STATUS_REVIEWED_COMPLETED_PREFIX)
    runs = values(STATUS_REVIEWED_RUN_URL_PREFIX)
    run_url = f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{owner['repo']}/actions/runs/{owner['run']}"
    return (len(timestamps) <= 1 and all(valid_review_timestamp(value) for value in timestamps)
            and runs == [quote(run_url, safe="")])


def owner_marker(owner):
    return f"{OWNER_PREFIX}{quote(json.dumps(owner, sort_keys=True, separators=(',', ':')), safe='')} -->"


def validate_owner(owner):
    fields = {"version", "repo", "pr", "run", "attempt", "kind", "target", "head", "base", "base_ref", "started", "generation"}
    if not isinstance(owner, dict) or set(owner) != fields or type(owner["version"]) is not int or owner["version"] != 1:
        raise ValueError("invalid presentation owner")
    if not isinstance(owner["repo"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", owner["repo"]):
        raise ValueError("invalid owner repository")
    for key in ("pr", "run", "attempt"):
        if type(owner[key]) is not int or not 0 < owner[key] <= 9007199254740991:
            raise ValueError("invalid owner integer")
    if owner["kind"] not in ("automatic", "issue_comment", "pull_request_review_comment"):
        raise ValueError("invalid owner target type")
    if owner["kind"] == "automatic":
        if owner["target"] is not None:
            raise ValueError("automatic owner has a trigger")
    elif type(owner["target"]) is not int or not 0 < owner["target"] <= 9007199254740991:
        raise ValueError("invalid owner target")
    for key in ("head", "base"):
        if not isinstance(owner[key], str) or not re.fullmatch(r"[0-9a-f]{40}", owner[key]):
            raise ValueError("invalid owner patch")
    if not isinstance(owner["base_ref"], str) or not owner["base_ref"] or len(owner["base_ref"]) > 255:
        raise ValueError("invalid owner base ref")
    if type(owner["started"]) is not int or not 0 < owner["started"] < 10**20:
        raise ValueError("invalid start token")
    identity = {key: value for key, value in owner.items() if key != "generation"}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if owner["generation"] != digest:
        raise ValueError("invalid owner generation")
    return owner


def captured_owner():
    owner = {
        "started": int(env("PRESENTATION_START")),
        "version": 1, "repo": env("REPO"), "pr": int(env("PR_NUMBER")),
        "run": int(env("GITHUB_RUN_ID")), "attempt": int(env("GITHUB_RUN_ATTEMPT")),
        "kind": env("TRIGGER_KIND", "automatic"),
        "target": int(env("TRIGGER_COMMENT_ID")) if env("TRIGGER_COMMENT_ID") else None,
        "head": env("HEAD_SHA"), "base": env("BASE_SHA"), "base_ref": env("BASE_REF"),
    }
    owner["generation"] = hashlib.sha256(json.dumps(owner, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return validate_owner(owner)


def status_owner(comment):
    matches = re.findall(re.escape(OWNER_PREFIX) + r"([A-Za-z0-9_.~%\-]+) -->", (comment or {}).get("body") or "")
    if len(matches) != 1:
        return None
    try:
        return validate_owner(json.loads(unquote(matches[0])))
    except (ValueError, TypeError, KeyError):
        return None


def status_owner_running(comment):
    matches = re.findall(re.escape(OWNER_PHASE_PREFIX) + r"(running|terminal) -->", (comment or {}).get("body") or "")
    if len(matches) == 1:
        return matches[0] == "running"
    return status_state(comment or {}) == "in_progress"


def owned_status(comment):
    return ((comment.get("user") or {}).get("login") == STATUS_AUTHOR
            and (comment.get("user") or {}).get("type") == "Bot"
            and STATUS_MARKER in (comment.get("body") or ""))


def stale_status_reason(reason):
    stale_reason = STATUS_REASONS["stale"]
    if reason == STATUS_REASONS["base_advanced"]:
        return stale_reason
    if reason and (reason == stale_reason or reason.startswith(stale_reason + " ")):
        return reason
    return stale_reason + (f" {reason}" if reason else "")


def terminal_status_reason(comment, owner):
    """Recover only fixed terminal copy from this trusted owner's receipt."""
    if not owned_status(comment) or status_owner(comment) != owner:
        return None
    body = comment.get("body") or ""
    phases = re.findall(re.escape(OWNER_PHASE_PREFIX) + r"(running|terminal) -->", body)
    reasons = re.findall(r"^\*\*Reason:\*\* (.+)$", body, re.MULTILINE)
    if phases != ["terminal"] or len(reasons) != 1:
        return None
    allowed = set(STATUS_REASONS.values()) | {
        f"{STATUS_REASONS['stale']} {text}" for key, text in STATUS_REASONS.items() if key != "stale"
    }
    return reasons[0] if reasons[0] in allowed else None


def trigger_reaction_path(owner):
    if owner["kind"] == "automatic":
        return None
    namespace = "issues" if owner["kind"] == "issue_comment" else "pulls"
    path = f"repos/{owner['repo']}/{namespace}/comments/{owner['target']}"
    comment = json.loads(gh_api([path]))
    link = "issue_url" if namespace == "issues" else "pull_request_url"
    parent = "issues" if namespace == "issues" else "pulls"
    expected = f"https://api.github.com/repos/{owner['repo']}/{parent}/{owner['pr']}"
    if (type(comment.get("id")) is not int or comment["id"] != owner["target"]
            or comment.get(link) != expected):
        raise ValueError("trigger comment does not belong to captured PR")
    return path + "/reactions"


def desired_pr_reaction(state, check_available):
    if state == "in_progress":
        return "eyes"
    if state == "success" and check_available:
        return "+1"
    return None


def reconcile_reactions(path, desired, guard, *, create=True):
    """The reserved bot/content pair is the only reaction ownership signal."""
    raw = gh_api(["--paginate", f"{path}?per_page=100"])
    owned = [reaction for page in parse_pages(raw) for reaction in page
             if (reaction.get("user") or {}).get("login") == STATUS_AUTHOR
             and reaction.get("content") in ("eyes", "+1")]
    removed, kept = True, False
    for reaction in owned:
        if reaction["content"] == desired and not kept:
            kept = True
            continue
        if type(reaction.get("id")) is not int or reaction["id"] <= 0:
            raise ValueError("invalid reaction ID")
        if not guard():
            return
        try:
            gh_api(["--method", "DELETE", f"{path}/{reaction['id']}"])
        except GhError:
            # DELETE may have succeeded despite the lost response. Confirm
            # absence before permitting a different reaction on this surface.
            current = parse_pages(gh_api(["--paginate", f"{path}?per_page=100"]))
            if any(item.get("id") == reaction["id"] for page in current for item in page):
                removed = False
                print("::warning::Could not remove an obsolete Claude Review reaction.")
    if desired and create and removed and not kept and guard():
        try:
            gh_api(["--method", "POST", path, "--input", "-"], {"content": desired})
        except GhError:
            # Never retry an ambiguous POST blindly. A future lifecycle step
            # reconciles the actual state; this readback diagnoses the result.
            current = parse_pages(gh_api(["--paginate", f"{path}?per_page=100"]))
            if not any((item.get("user") or {}).get("login") == STATUS_AUTHOR
                       and item.get("content") == desired for page in current for item in page):
                raise


def live_pr_projection(repo, pr_number):
    """The PR's base SHA is a comparison projection, not branch-tip authority."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid base repository")
    current = with_retries(
        lambda: gh_api([f"repos/{repo}/pulls/{pr_number}"]),
        "Reading the current PR head and base",
    )
    pr = json.loads(current, object_pairs_hook=unique_json_object)
    head_sha, base_ref = pr["head"]["sha"], pr["base"]["ref"]
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError("the live PR head SHA is invalid")
    if not isinstance(base_ref, str) or not base_ref or len(base_ref) > 255:
        raise ValueError("the live PR base ref is invalid")
    if pr["base"]["repo"]["full_name"] != repo:
        raise ValueError("the live PR base repository differs from the requested repository")
    base_sha = pr["base"]["sha"]
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("the live PR comparison base SHA is invalid")
    return head_sha, base_ref, base_sha


def direct_base_tip(repo, base_ref):
    raw = with_retries(
        lambda: gh_api([f"repos/{repo}/git/ref/heads/{quote(base_ref, safe='')}"]),
        "Reading the current base branch tip",
    )
    ref = json.loads(raw, object_pairs_hook=unique_json_object)
    if (ref["ref"] != f"refs/heads/{base_ref}" or ref["object"]["type"] != "commit"
            or not isinstance(ref["object"]["sha"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", ref["object"]["sha"])):
        raise ValueError("the direct base branch ref is invalid")
    return ref["object"]["sha"]


def live_pr_identity(repo, pr_number, *, include_base_sha=False, require_projection_match=False):
    before = live_pr_projection(repo, pr_number)
    head_sha, base_ref, _ = before
    if include_base_sha:
        base_sha = direct_base_tip(repo, base_ref)
        if live_pr_projection(repo, pr_number) != before:
            raise ValueError("the PR changed while resolving its base branch tip")
        if require_projection_match and before[2] != base_sha:
            raise ValueError("the PR comparison base and direct branch tip disagree")
        return head_sha, base_ref, base_sha
    return head_sha, base_ref


def cmd_admit_manual():
    """Fail closed before model execution; never rewrite the captured snapshot."""
    global PRESENTATION_DEADLINE
    PRESENTATION_DEADLINE = time.monotonic() + PRESENTATION_SECONDS
    try:
        repo, number = env("REPO"), env("PR_NUMBER")
        captured = (env("HEAD_SHA"), env("BASE_REF"), env("BASE_SHA"))
        projected = live_pr_projection(repo, number)
        if projected != captured:
            raise ValueError("the PR no longer matches the captured manual snapshot")
        tip = direct_base_tip(repo, projected[1])
        if tip != captured[2]:
            raise ValueError("the PR comparison base differs from the current base branch tip")
        if live_pr_projection(repo, number) != projected:
            raise ValueError("the PR changed during manual freshness admission")
        return 0
    except (GhError, ValueError, KeyError, TypeError) as error:
        print(f"::error::Cannot admit manual review: {error}")
        return 1
    finally:
        PRESENTATION_DEADLINE = None


def status_publication_identity(comment):
    """Compare writable status identity, ignoring unrelated API metadata."""
    if comment is None:
        return None
    author = comment.get("user", {})
    return comment.get("id"), author.get("login"), author.get("type"), comment.get("body")


def best_effort_status(head_sha, state, *, check_available=True, acquire=False, completed_at=None,
                       check_completed_at=False):
    """Only trusted starts acquire; every later write requires that exact owner.

    The workflow's shared PR queue serializes writers. API reads before writes
    also detect changed patch identity and publication from another generation.
    """
    global PRESENTATION_DEADLINE
    if env("STATUS_COMMENTS_ENABLED") != "true" or not env("PR_NUMBER"):
        return
    if state in ("success", "action_required") and not check_available:
        state = "publication_incomplete"
    PRESENTATION_DEADLINE = time.monotonic() + PRESENTATION_SECONDS
    try:
        repo, pr_number, base_ref = env("REPO"), env("PR_NUMBER"), env("BASE_REF")
        stale = state == "stale"
        acquiring_running = acquire and state == "in_progress"
        if not acquire and not stale and not env("PRESENTATION_OWNER"):
            # An intentionally suppressed or unavailable start has no receipt.
            # Its diagnostic output never grants finalization write authority.
            outcome = env("PRESENTATION_OUTCOME")
            reasons = {
                "suppressed_patch_changed": "captured head/base was already superseded",
                "suppressed_newer_owner": "a newer attempt already owned presentation",
                "suppressed_presentation_changed": "presentation changed before publication",
            }
            if outcome in reasons:
                print(f"::notice::Claude Review presentation suppressed at start: {reasons[outcome]}. "
                      "No owner was acquired; the captured Check remains authoritative.")
            else:
                print("::warning::Claude Review presentation unavailable: no owner receipt from start "
                      f"({outcome if outcome in ('acquired', 'unavailable') else 'unknown outcome'}). "
                      "The captured Check remains authoritative; finalization will not acquire ownership.")
            return "suppressed" if outcome in reasons else "unavailable"
        if not acquire and not stale:
            try:
                owner = validate_owner(json.loads(env("PRESENTATION_OWNER")))
            except (ValueError, TypeError, KeyError):
                print("::warning::Claude Review presentation unavailable: malformed owner receipt. "
                      "The captured Check remains authoritative; no presentation writes were attempted.")
                return "unavailable"
        existing = find_status_comment(repo, pr_number)
        prior = status_owner(existing)
        if stale:
            if not prior or (prior["repo"], str(prior["pr"])) != (repo, pr_number):
                raise ValueError("stale refresh has no verified owner")
            owner = prior
            expected_patch = (head_sha, base_ref)
            live = live_pr_identity(repo, pr_number, include_base_sha=True)
            if live[:2] != expected_patch:
                return
            if ((status_head(existing), status_base_ref(existing)) == expected_patch
                    and status_state(existing) != "stale" and live[2] == owner["base"]):
                return
            expected_patch = live
        else:
            if acquire:
                owner = captured_owner()
            if (owner["repo"], str(owner["pr"]), owner["head"], owner["base"], owner["base_ref"],
                    str(owner["run"]), str(owner["attempt"])) != (
                    repo, pr_number, head_sha, env("BASE_SHA"), base_ref,
                    env("GITHUB_RUN_ID"), env("GITHUB_RUN_ATTEMPT")):
                raise ValueError("captured owner differs from executing attempt")
            if not acquire and prior != owner:
                if prior:
                    print("::notice::Claude Review presentation suppressed: this attempt no longer owns presentation.")
                else:
                    print("::warning::Claude Review presentation unavailable: persisted owner is missing or malformed.")
                    return "unavailable"
                return
            # An older start must not reclaim a later generation. A higher
            # attempt of the same run is accepted only by this acquisition path.
            if acquire and prior != owner and prior and owner["started"] <= prior["started"]:
                write_output("presentation_outcome", "suppressed_newer_owner")
                print("::notice::Claude Review presentation suppressed: older start cannot reclaim newer presentation.")
                return
            if acquire and prior == owner and status_state(existing) != "in_progress":
                write_output("presentation_owner", json.dumps(owner, separators=(",", ":")))
                write_output("status_comment_id", existing["id"])
                write_output("presentation_outcome", "acquired")
                return
            expected_patch = (head_sha, base_ref, env("BASE_SHA"))
        # A refresh has no execution verdict of its own. Preserve only this
        # owner's allowlisted terminal reason; acquisition starts a new lifecycle.
        reason = terminal_status_reason(existing, owner) if stale else None
        if stale:
            reason = stale_status_reason(reason)
        if state == "failure":
            if env("REVIEW_RESULT") == "cancelled":
                reason = STATUS_REASONS["cancelled"]
            elif acquire or env("START_RESULT", "success") != "success":
                reason = STATUS_REASONS["preparation_failed"]
            else:
                reason = STATUS_REASONS["incomplete"]
        elif state == "publication_incomplete":
            reason = STATUS_REASONS["check_publication_failed"]
        projection_must_match = not stale
        current = lambda: live_pr_identity(repo, pr_number, include_base_sha=True,
                                          require_projection_match=projection_must_match) == expected_patch
        last_review = last_completed_review(existing) if existing else None
        last_review_details = last_completed_review_details(existing)
        completed_owner_verified = (
            state in ("success", "action_required") and check_available
            or stale and completed_review_for_owner(existing, owner)
        )
        if (stale and not completed_owner_verified and not status_owner_running(existing)
                and last_review_details.get("generation") == owner["generation"]
                and reason == STATUS_REASONS["stale"]):
            reason = stale_status_reason(STATUS_REASONS["completion_metadata_unverified"])
        if state in ("success", "action_required") and check_available:
            # A terminal owner can still carry a previous review's history after
            # failure. Reuse a timestamp only when its completed generation matches.
            same_completion = last_review_details.get("generation") == owner["generation"]
            if (prior == owner and not status_owner_running(existing)
                    and last_review == (head_sha, base_ref, state)
                    and same_completion):
                stored_time = last_review_details.get("completed_at")
                if not check_completed_at or stored_time is None:
                    completed_at = stored_time
        live = live_pr_identity(repo, pr_number, include_base_sha=True)
        if (live != expected_patch and not stale
                and (acquire or (prior == owner and status_owner_running(existing)))):
            # The Check and owner retain their captured patch. Only the visible
            # current identity follows movement; it must never imply approval.
            # The newer-owner gate above still prevents old starts/finalizers
            # from replacing another accepted attempt's presentation.
            if state in ("success", "action_required") and check_available:
                last_review = (head_sha, base_ref, state)
                last_review_details = {"base_sha": owner["base"], "run_url": env("DETAILS_URL"),
                                       "completed_at": completed_at, "generation": owner["generation"]}
            state = "stale"
            reason = stale_status_reason(reason)
            head_sha, base_ref = live[:2]
            expected_patch = live
            projection_must_match = False
        if live != expected_patch:
            if acquire:
                write_output("presentation_outcome", "suppressed_patch_changed")
                print("::notice::Claude Review presentation suppressed: captured head/base is no longer current; "
                      "no owner was acquired.")
            else:
                print("::notice::Claude Review presentation suppressed: captured head/base is no longer current.")
            if not acquire and not stale and prior == owner and status_owner_running(existing):
                try:
                    path = trigger_reaction_path(owner)
                    if path:
                        reconcile_reactions(path, None, lambda: status_owner(find_status_comment(repo, pr_number)) == owner)
                except Exception as error:
                    print(f"::warning::Could not clear superseded trigger reactions: {error}")
            return
        # Re-read the observed owner immediately before changing shared status.
        observed = find_status_comment(repo, pr_number)
        if status_publication_identity(observed) != status_publication_identity(existing):
            if acquire:
                write_output("presentation_outcome", "suppressed_presentation_changed")
            print("::notice::Claude Review presentation suppressed: presentation changed before status publication.")
            return
        publication_patch = live_pr_identity(repo, pr_number, include_base_sha=True)
        if publication_patch == expected_patch and projection_must_match and not current():
            raise ValueError("captured patch authority changed before status publication")
        if publication_patch != expected_patch:
            if not stale and (acquire or (prior == owner and status_owner_running(existing))):
                if state in ("success", "action_required") and check_available:
                    last_review = (owner["head"], owner["base_ref"], state)
                    last_review_details = {"base_sha": owner["base"], "run_url": env("DETAILS_URL"),
                                           "completed_at": completed_at, "generation": owner["generation"]}
                state = "stale"
                reason = stale_status_reason(reason)
                head_sha, base_ref = publication_patch[:2]
                expected_patch = publication_patch
                projection_must_match = False
                if not current():
                    raise ValueError("PR identity kept changing before status publication")
            else:
                if acquire:
                    write_output("presentation_outcome", "suppressed_patch_changed")
                print("::notice::Claude Review presentation suppressed: captured head/base changed before status publication.")
                return
        body = {"body": status_comment_body(
            head_sha, base_ref, state, check_available=check_available,
            last_review=last_review, last_review_details=last_review_details, base_sha=expected_patch[2],
            trigger_label=env("TRIGGER_LABEL"), owner=owner,
            owner_running=status_owner_running(existing) if stale else acquiring_running if state == "stale" else None,
            reason=reason, completed_at=completed_at,
            completed_owner_verified=completed_owner_verified)}
        published = False
        try:
            if existing:
                comment_id = existing["id"]
                gh_api(["--method", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "--input", "-"], body)
            elif acquire:
                created = json.loads(gh_api(["--method", "POST", f"repos/{repo}/issues/{pr_number}/comments", "--input", "-"], body))
                comment_id = created["id"]
            else:
                raise ValueError("only a start may create presentation")
        except (GhError, ValueError, KeyError) as error:
            print(f"::warning::Could not publish Claude Review status comment: {error}")
        # Both successful and ambiguous writes require persisted-owner readback.
        confirmed = find_status_comment(repo, pr_number)
        if not confirmed or status_owner(confirmed) != owner:
            raise ValueError("presentation owner publication could not be confirmed")
        published = confirmed.get("body") == body["body"]
        if acquire:
            if not published:
                raise ValueError("start presentation publication could not be confirmed")
            write_output("presentation_owner", json.dumps(owner, separators=(",", ":")))
            write_output("presentation_outcome", "acquired")
        write_output("status_comment_id", confirmed["id"])
        def guard(require_patch=True):
            return (status_publication_identity(find_status_comment(repo, pr_number)) ==
                    status_publication_identity(confirmed)
                    and (not require_patch or current()))
        desired = desired_pr_reaction(state, check_available) if published else None
        if published and state == "stale":
            if status_owner_running(confirmed):
                desired = "eyes"
            elif (check_available and completed_owner_verified
                  and completed_review_for_owner(confirmed, owner)
                  and last_completed_review(confirmed)[2] == "success"):
                desired = "+1"
        surfaces = [(f"repos/{repo}/issues/{pr_number}/reactions", True)]
        try:
            path = trigger_reaction_path(owner)
            if path:
                surfaces.append((path, False))
        except Exception as error:
            print(f"::warning::Could not verify Claude Review trigger: {error}")
        for path, opening in surfaces:
            try:
                # A refresh may retain a clean historical signal, but cannot
                # recreate one cleared by a later unsuccessful publication.
                create = not (stale and desired == "+1")
                if opening:
                    if guard():
                        reconcile_reactions(path, desired, lambda: guard(), create=create)
                elif guard(require_patch=False):
                    trigger_desired = desired if current() else None
                    reconcile_reactions(path, trigger_desired,
                                        lambda: guard(require_patch=trigger_desired is not None), create=create)
            except Exception as error:
                print(f"::warning::Could not reconcile Claude Review reactions: {error}")
        return "published" if published else "unavailable"
    except Exception as error:
        if acquire:
            try:
                write_output("presentation_outcome", "unavailable")
            except Exception:
                print("::warning::Could not export the Claude Review presentation diagnostic.")
        print(f"::warning::Claude Review presentation unavailable: {error}")
        return "unavailable"
    finally:
        PRESENTATION_DEADLINE = None


def completion_marker(repo, pr_number, head_sha, base_ref, base_sha, merge_base_sha):
    identity = [repo, str(pr_number), head_sha, base_ref, base_sha, merge_base_sha]
    encoded = quote(json.dumps(identity, separators=(",", ":")), safe="")
    return f"{COMPLETION_PREFIX}{encoded} -->"


def completion_comment_body(repo, head_sha, base_ref, base_sha, marker, details_url):
    return (
        f"{marker}\n"
        "🎉 Claude review completed—no findings on "
        f"[`{head_sha[:7]}`](https://github.com/{repo}/commit/{head_sha}).\n\n"
        f"Compared against {inline_code(base_ref)} at "
        f"[`{base_sha[:7]}`](https://github.com/{repo}/commit/{base_sha}) · "
        f"[Review run]({details_url})"
    )


def best_effort_manual_completion(head_sha):
    """Publish historical UX only after this run's clean Check was published."""
    if env("MANUAL_COMPLETION_ENABLED") != "true":
        return
    global PRESENTATION_DEADLINE
    PRESENTATION_DEADLINE = time.monotonic() + PRESENTATION_SECONDS
    repo, pr_number = env("REPO"), env("PR_NUMBER")
    base_ref, base_sha = env("BASE_REF"), env("BASE_SHA")
    merge_base_sha, details_url = env("MERGE_BASE_SHA"), env("DETAILS_URL")
    try:
        if (not repo or not pr_number.isdigit() or not base_ref or not details_url
                or any(not re.fullmatch(r"[0-9a-f]{40}", sha)
                       for sha in (head_sha, base_sha, merge_base_sha))):
            raise ValueError("the captured completion identity or workflow URL is missing or invalid")
        marker = completion_marker(repo, pr_number, head_sha, base_ref, base_sha, merge_base_sha)
        path = f"repos/{repo}/issues/{pr_number}/comments"
        raw = with_retries(
            lambda: gh_api(["--paginate", f"{path}?per_page=100"]),
            "Looking up a manual Claude Review completion notice",
        )
        if any(
            (comment.get("user") or {}).get("login") == STATUS_AUTHOR
            and (comment.get("user") or {}).get("type") == "Bot"
            and marker in (comment.get("body") or "")
            for page in parse_pages(raw) for comment in page
        ):
            return
        # Read after discovery, immediately before POST. GitHub cannot make
        # this read/write atomic, so the notice describes the captured history
        # and never claims that it covers the PR's current patch.
        if live_pr_identity(repo, pr_number, include_base_sha=True,
                            require_projection_match=True) != (head_sha, base_ref, base_sha):
            print("::notice::Skipping manual completion notice for a superseded review snapshot.")
            return
        body = completion_comment_body(repo, head_sha, base_ref, base_sha, marker, details_url)
        # Never retry an ambiguous POST. A subsequent clean rerun can discover
        # a notice that was created despite a failed response.
        gh_api(["--method", "POST", path, "--input", "-"], {"body": body})
    except Exception as error:
        print(f"::warning::Could not publish manual Claude Review completion notice: {error}")
    finally:
        PRESENTATION_DEADLINE = None


def summary_lines(head_sha, sentence, *, completed_at=None):
    lines = [
        f"**Reviewed commit:** `{head_sha}`",
        f"**Trigger:** {env('TRIGGER_LABEL', 'Claude review')}",
        "",
        sentence,
    ]
    if completed := valid_review_timestamp(completed_at):
        lines += ["", f"**Completed:** {relative_time(completed)}"]
    if env("DETAILS_URL"):
        lines += ["", f"[Workflow run]({env('DETAILS_URL')})"]
    return bounded_string("\n".join(lines), CHECK_SUMMARY_BYTES)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def review_identity(head_sha):
    """What a later run needs to tell whether this run reviewed the same diff."""
    pr_number = env("PR_NUMBER")
    return {
        "reviewed_sha": head_sha,
        "pr_number": int(pr_number) if pr_number.isdigit() else pr_number or None,
        "base_sha": env("BASE_SHA") or None,
        "merge_base_sha": env("MERGE_BASE_SHA") or None,
        "runtime_repository": env("RUNTIME_REPOSITORY") or None,
        "runtime_sha": env("RUNTIME_SHA") or None,
        "runtime_workflow_path": env("RUNTIME_WORKFLOW_PATH") or None,
    }


def bounded_string(value, limit):
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit - len("…".encode("utf-8"))].decode("utf-8", errors="ignore") + "…"


def bounded_json_string(value, limit):
    # JSON escaping can expand even ASCII control characters sixfold; bound the
    # encoded scalar, rather than assuming its UTF-8 source size is sufficient.
    def size(text):
        return len(json.dumps(text, ensure_ascii=False).encode("utf-8"))

    if size(value) <= limit:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if size(value[:middle] + "…") <= limit:
            low = middle
        else:
            high = middle - 1
    return value[:low] + "…"


def evidence_text(evidence):
    """Bound diagnostics without treating a digest as finding-ID membership.

    Included IDs retain exact attribution. Omitted IDs remain unknown to later
    runs and therefore fail closed when rediscovered. The separately persisted
    same-diff presence flag preserves stickiness even after all comments are
    deleted, through failed reviews and fallback publication. Counts and hashes
    describe each full observed set; they never prove membership or diff scope.
    Captured PR/SHA/runtime identity generated by these workflows fits the scalar
    bounds unchanged.
    """
    bounded = dict(evidence)
    for field in EVIDENCE_ID_FIELDS:
        values = evidence.get(field)
        if values is None:
            continue
        values = sorted(set(values))
        bounded[field] = values[:EVIDENCE_ID_LIMIT]
        bounded[field + "_summary"] = {
            "count": len(values),
            "sha256": hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest(),
            "truncated": len(values) > EVIDENCE_ID_LIMIT,
        }
    for field, value in list(bounded.items()):
        if isinstance(value, str):
            bounded[field] = bounded_json_string(value, EVIDENCE_STRING_BYTES)
    if "evidence_errors" in bounded:
        bounded["evidence_errors"] = [
            bounded_json_string(error, EVIDENCE_ERROR_BYTES)
            for error in bounded["evidence_errors"][:4]
        ]
    return "```json\n" + json.dumps(bounded, indent=2, ensure_ascii=False) + "\n```"


def publish_missing_check_failure(repo, head_sha):
    """Record a completed failure when start-check never created a check run.

    Retrying this POST can at worst add a duplicate completed failure, never a
    run stuck in_progress.
    """
    body = {
        "name": CHECK_NAME,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "failure",
        "completed_at": now(),
        "output": {
            "title": "Review did not start",
            "summary": summary_lines(
                head_sha, "The review could not be started, so Claude did not review this commit."
            ),
            "text": evidence_text(
                {**review_identity(head_sha), "start_check_result": env("START_RESULT") or None}
            ),
        },
    }
    for key, name in (("details_url", "DETAILS_URL"), ("external_id", "EXTERNAL_ID")):
        if env(name):
            body[key] = env(name)
    try:
        with_retries(
            lambda: gh_api(["--method", "POST", f"repos/{repo}/check-runs", "--input", "-"], body),
            "Publishing a failure for a review that never started",
        )
        print("::error::start-check created no check run; published a completed failure instead.")
        best_effort_status(head_sha, "failure")
    except GhError:
        print("::error::No Claude Review check run exists and a failure could not be published.")
        best_effort_status(head_sha, "publication_incomplete")
    return 1


def cmd_create():
    repo, head_sha = env("REPO"), env("HEAD_SHA")
    is_fork = env("IS_FORK") == "true"
    body = {
        "name": CHECK_NAME,
        "head_sha": head_sha,
        "status": "in_progress",
        "started_at": now(),
        "output": {
            "title": "Review in progress",
            "summary": summary_lines(head_sha, "Claude is reviewing this commit."),
        },
    }
    if env("DETAILS_URL"):
        body["details_url"] = env("DETAILS_URL")
    if env("EXTERNAL_ID"):
        body["external_id"] = env("EXTERNAL_ID")

    # A failed-looking POST may still have created the run, and external_id is
    # not an idempotency key. After any such POST, look the run up by its
    # external_id before POSTing again, and never POST while that lookup is
    # itself failing. This greatly reduces duplicates but cannot rule them out:
    # a lookup can succeed before GitHub lists a just-created run, and the
    # resulting second run is the one reported and completed while the first
    # stays in_progress.
    unreconciled_post = False

    def create_once():
        nonlocal unreconciled_post
        if unreconciled_post:
            existing = find_check_run_by_external_id(repo, head_sha, body.get("external_id"))
            if existing is not None:
                return existing
            unreconciled_post = False
        unreconciled_post = bool(body.get("external_id"))
        created = gh_api(["--method", "POST", f"repos/{repo}/check-runs", "--input", "-"], body)
        unreconciled_post = False
        return json.loads(created)["id"]

    try:
        check_run_id = with_retries(create_once, "Creating the Claude Review check run")
    except (GhError, ValueError, KeyError) as error:
        if is_fork:
            print(
                "::warning::Could not create a Claude Review check run for this fork PR "
                f"({error}); the review continues with inline comments as its only evidence."
            )
            write_output("check_run_id", "")
            best_effort_status(head_sha, "in_progress", check_available=False, acquire=True)
            return 0
        print(f"::error::Could not create the Claude Review check run: {error}")
        best_effort_status(head_sha, "failure", check_available=False, acquire=True)
        return 1
    write_output("check_run_id", check_run_id)
    print(f"Created Claude Review check run {check_run_id} for {head_sha}")
    best_effort_status(head_sha, "in_progress", acquire=True)
    return 0


def cmd_snapshot(output_path):
    ids = fetch_claude_comment_ids(env("REPO"), env("PR_NUMBER"), env("HEAD_SHA"))
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(ids, handle)
    print(f"Recorded {len(ids)} existing Claude review comment(s) on {env('HEAD_SHA')}")
    return 0


def patch_check_run(repo, check_run_id, body, description):
    with_retries(
        lambda: gh_api(
            ["--method", "PATCH", f"repos/{repo}/check-runs/{check_run_id}", "--input", "-"], body
        ),
        description,
    )


def cmd_finalize():
    repo, pr_number, head_sha = env("REPO"), env("PR_NUMBER"), env("HEAD_SHA")
    if not head_sha:
        # Without a resolved head there is no commit to look up or attach
        # exact-SHA state to, so fail before any GitHub call.
        print(
            "::error::The reviewed head SHA was never resolved, so no exact-SHA "
            "Claude Review check run can record this failure."
        )
        return 1
    check_run_id = env("CHECK_RUN_ID")
    if not check_run_id and env("EXTERNAL_ID"):
        # start-check may have failed after an unconfirmed POST created the run.
        # Only a successful lookup can show it is absent; an unresolved lookup
        # must not lead to another POST.
        try:
            found = with_retries(
                lambda: find_check_run_by_external_id(repo, head_sha, env("EXTERNAL_ID")),
                "Looking up this run's Claude Review check run",
            )
        except GhError:
            if env("IS_FORK") == "true":
                print("::warning::Could not look up this fork PR's Claude Review check run.")
                best_effort_status(head_sha, "publication_incomplete", check_available=False)
                return 0
            print(
                "::error::Could not confirm whether start-check created a Claude Review "
                "check run, so none was created or completed."
            )
            best_effort_status(head_sha, "publication_incomplete")
            return 1
        check_run_id = "" if found is None else str(found)
    if not check_run_id:
        if env("IS_FORK") == "true":
            print(
                "::notice::No Claude Review check run exists for this fork PR; "
                "its inline comments remain the only review evidence."
            )
            best_effort_status(head_sha, "publication_incomplete", check_available=False)
            return 0
        return publish_missing_check_failure(repo, head_sha)

    review_result, action_conclusion = env("REVIEW_RESULT"), env("ACTION_CONCLUSION")
    start_result = env("START_RESULT", "success")
    # The action's success conclusion only reflects the first result Claude
    # Code emitted; the review job's verdict from the execution record is what
    # shows the review itself finished.
    completion_verified = env("COMPLETION_VERIFIED") == "true"
    completion_reason = env("COMPLETION_REASON")
    review_completed = (
        start_result == "success"
        and review_result == "success"
        and action_conclusion == "success"
        and completion_verified
    )
    merge_base_sha = env("MERGE_BASE_SHA")

    # Each source is read independently. The snapshot attributes newly posted
    # comments to this run's captured diff; trusted Check history attributes
    # preexisting comments. A raw head SHA alone cannot identify their diff.
    errors = []
    history = []
    try:
        history, (prior_ids, recorded_ids, other_diff_ids) = fetch_prior_finding_evidence(
            repo, head_sha, check_run_id, pr_number, merge_base_sha
        )
    except GhError as error:
        prior_ids, recorded_ids, other_diff_ids = [], set(), set()
        errors.append(f"earlier Claude Review history is unavailable ({error})")

    observed_ids = None
    try:
        observed_ids = fetch_claude_comment_ids(repo, pr_number, head_sha)
    except GhError as error:
        errors.append(f"Claude review comments on this commit are unavailable ({error})")

    before = None
    before_file = env("BEFORE_IDS_FILE")
    if review_completed or os.path.exists(before_file):
        try:
            with open(before_file, encoding="utf-8") as handle:
                snapshot = json.load(handle)
                if not valid_comment_id_list(snapshot):
                    raise ValueError("expected a unique list of positive comment IDs")
                before = set(snapshot)
        except (OSError, ValueError) as error:
            errors.append(f"the pre-review comment snapshot is unavailable ({error})")

    observed = set(observed_ids or [])
    new_ids = sorted(observed - before) if before is not None else []
    scoped_ids = recorded_ids | set(new_ids)
    earlier_ids = sorted(recorded_ids - set(new_ids))
    unscoped_ids = sorted(observed - scoped_ids - other_diff_ids)
    if unscoped_ids:
        errors.append(
            "earlier Claude finding comments cannot be attributed to a reviewed diff "
            f"(comment IDs: {', '.join(map(str, unscoped_ids))})"
        )

    publication = None
    if review_completed:
        try:
            publication = publication_receipts(env("FINDING_PUBLICATION"))
        except (ValueError, TypeError) as error:
            errors.append(f"finding publication receipts are unavailable or invalid ({error})")
        if publication is not None:
            # A receipt must be newly published on this captured PR/head by the
            # trusted Claude bot. A prior ID or an unrelated new comment cannot
            # stand in for an absent receipt. Keep actual findings even on failure.
            missing_ids = sorted(set(publication["comment_ids"]) - set(new_ids))
            if missing_ids:
                errors.append("finding publication could not be confirmed "
                              f"(comment IDs: {', '.join(map(str, missing_ids))})")

    # A completed review with incomplete evidence fails closed; after a failed
    # review the conclusion is already failure, so gaps are only warnings.
    evidence_error = "; ".join(errors) if errors and review_completed else None
    if not review_completed:
        for error in errors:
            print(f"::warning::{error}")

    conclusion, title, sentence = decide(
        review_result,
        action_conclusion,
        new_ids,
        earlier_ids,
        prior_ids,
        evidence_error,
        start_result,
        completion_verified,
        completion_reason,
    )
    evidence = {
        **review_identity(head_sha),
        "trigger": env("TRIGGER_LABEL"),
        "start_check_result": start_result,
        "presentation_outcome": env("PRESENTATION_OUTCOME") if env("PRESENTATION_OUTCOME") in (
            "acquired", "unavailable", "suppressed_patch_changed", "suppressed_newer_owner",
            "suppressed_presentation_changed") else None,
        "review_job_result": review_result,
        "action_conclusion": action_conclusion,
        "completion_verified": completion_verified,
        "completion_reason": completion_reason or None,
        "finding_publication": publication,
        "same_diff_findings_recorded": bool(scoped_ids or prior_ids),
        "new_finding_comment_ids": new_ids,
        "claude_finding_comment_ids": sorted(scoped_ids),
        "observed_head_comment_ids": observed_ids,
        "unscoped_finding_comment_ids": unscoped_ids,
        "other_diff_finding_comment_ids": sorted(observed & other_diff_ids),
        "prior_finding_check_run_ids": prior_ids,
        "evidence_errors": errors,
    }
    completed_at = completed_check_timestamp(history, check_run_id, head_sha, conclusion) or now()
    body = {
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": completed_at,
        "output": {
            "title": title,
            "summary": bounded_string((
                "The shared status comment was unavailable at review start; consult this Check and workflow run.\n\n"
                if env("PRESENTATION_OUTCOME") == "unavailable" else "") + summary_lines(
                    head_sha, sentence,
                    completed_at=completed_at if conclusion in ("success", "action_required") else None),
                CHECK_SUMMARY_BYTES),
            "text": evidence_text(evidence),
        },
    }
    try:
        patch_check_run(repo, check_run_id, body, "Publishing the Claude Review result")
    except GhError:
        fallback = {
            "status": "completed",
            "conclusion": "failure",
            "output": {
                "title": "Review outcome could not be published",
                "summary": summary_lines(
                    head_sha,
                    "The review outcome could not be published; treat this commit as not reviewed.",
                ),
                # Keep the evidence so recorded findings stay sticky for later runs.
                "text": body["output"]["text"],
            },
        }
        try:
            patch_check_run(repo, check_run_id, fallback, "Publishing the fallback failure state")
            print("::error::Published a fallback failure state instead of the review outcome.")
            best_effort_status(head_sha, "publication_incomplete")
        except GhError:
            print("::error::Could not finalize the Claude Review check run; it may remain in progress.")
            best_effort_status(head_sha, "publication_incomplete")
        return 1
    print(f"Claude Review check run {check_run_id}: {conclusion} ({title})")
    presentation_outcome = best_effort_status(head_sha, conclusion, completed_at=body["completed_at"],
                                             check_completed_at=True)
    if presentation_outcome == "unavailable":
        body["output"]["summary"] = bounded_string(
            "The shared status comment could not be updated; consult this Check and workflow run.\n\n" +
            body["output"]["summary"], CHECK_SUMMARY_BYTES)
        try:
            # One bounded PATCH; do not let informational presentation failures
            # change the captured review conclusion or finding evidence.
            gh_api(["--method", "PATCH", f"repos/{repo}/check-runs/{check_run_id}", "--input", "-"], body)
        except GhError:
            print("::warning::Could not record status comment publication failure in the Check.")
    if conclusion == "success":
        best_effort_manual_completion(head_sha)
    if evidence_error:
        print(f"::error::{evidence_error}")
        return 1
    return 0


def cmd_stale():
    head_sha, base_ref = env("HEAD_SHA"), env("BASE_REF")
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha) or not base_ref or not env("PR_NUMBER"):
        print("::error::A current PR head SHA, base ref, and PR number are required for stale status refresh.")
        return 1
    # Queue order is not event order. A delayed head or base-ref event must
    # not replace a newer status. This live read is only for comment UX.
    try:
        live_identity = live_pr_identity(env("REPO"), env("PR_NUMBER"))
    except (GhError, ValueError, KeyError, TypeError) as error:
        print(f"::warning::Could not verify the current PR identity for stale status: {error}")
        return 0
    if live_identity != (head_sha, base_ref):
        print(f"::notice::PR event for {head_sha} on {base_ref} was superseded by {live_identity}.")
        return 0
    best_effort_status(head_sha, "stale")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create")
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--output", required=True)
    sub.add_parser("finalize")
    sub.add_parser("stale")
    sub.add_parser("admit-manual")
    args = parser.parse_args(argv)
    if args.command == "create":
        return cmd_create()
    if args.command == "snapshot":
        try:
            return cmd_snapshot(args.output)
        except GhError as error:
            print(f"::error::Could not record existing Claude review comments: {error}")
            return 1
    if args.command == "stale":
        return cmd_stale()
    if args.command == "admit-manual":
        return cmd_admit_manual()
    return cmd_finalize()


if __name__ == "__main__":
    sys.exit(main())
