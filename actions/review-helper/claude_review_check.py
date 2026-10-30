#!/usr/bin/env python3
"""Publish the trusted `Claude Review` Check Run for one reviewed PR snapshot.

Runs only in workflow jobs that never invoke Claude. The check run is the
authoritative per-commit review state; the model never writes it.

Subcommands (configured through environment variables set by the workflow):
  create    open an in_progress check run for HEAD_SHA
  snapshot  record Claude's review-comment IDs on HEAD_SHA before the review
  finalize  map the review outcome to a conclusion and complete the check run
  stale     refresh an existing status comment for a changed PR snapshot
  discover-target  list bounded open PRs targeting a pushed branch
  refresh-target   refresh one affected PR without executing a review
  admit-manual  validate a manual snapshot without replacing its captured baseline
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
_TRIGGER_FROM_LABEL = object()
ATTEMPT_TRIGGER_PREFIX = "<!-- claude-review-runtime:attempt-trigger:"
STATUS_REVIEWED_TRIGGER_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-trigger:"
STATUS_REVIEWED_SELECTION_PREFIX = "<!-- claude-review-runtime:claude-review-last-reviewed-selection:"
REVIEW_TRIGGERS = {"PR opened for review", "Draft marked ready", "Manual request"}
STATUS_AUTHOR = "github-actions[bot]"
INLINE_FAILURE_PREFIX = "<!-- claude-review-runtime:inline-tool-failure:"
OWNER_PHASE_PREFIX = "<!-- claude-review-runtime:presentation-owner-phase:"
OWNER_PREFIX = "<!-- claude-review-runtime:presentation-owner:"
STATUS_REASONS = {
    "cancelled": "The review was cancelled before verified completion.",
    "preparation_failed": "Review preparation failed; Claude did not complete this review.",
    "incomplete": "The review did not complete with reliable execution and finding evidence.",
    "inline_tool_failed": "An inline-comment tool call failed.",
    "unread_inputs": "Required review inputs were not fully read.",
    "workflow_changed": "The captured patch changes the review workflow; the cause of the incomplete review is unknown.",
    "check_publication_failed": "The authoritative Check result could not be published.",
    "tooling_unavailable": "Trusted review tooling was unavailable; the review could not be finalized reliably.",
    "stale": "The captured head or base was superseded; the current patch is not reviewed by this attempt.",
    "base_advanced": "The target baseline differs; integration with the current baseline has not been reviewed.",
    "completion_metadata_unverified": "The stored presentation metadata cannot establish a completed result for this attempt.",
}
LEGACY_BASE_ADVANCED_REASON = "The target branch advanced; integration with the current baseline has not been reviewed."
CAPTURED_PATCH_SCOPE = "This review covers the captured patch only; integration with other target baselines is not covered."
# Recovery guidance follows main; captured review evidence stays immutable.
RECOVERY_GUIDE = "https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md"
INCOMPLETE_RECOVERY = RECOVERY_GUIDE + "#installation-and-incomplete-reviews"
UNREAD_RECOVERY = RECOVERY_GUIDE + "#incomplete-captured-input-reads"
TARGET_TIP_WARNING = "⚠️ The target branch differs from the reviewed baseline. Integration with its current version hasn’t been reviewed."
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
# finalize may look up an unreported run, read history and a retained Check
# independently, then read comments and PATCH the result and its fallback.
# The tests check the implementation against
# this model and the start-check and publish-status job timeouts against the
# budget it implies, so publication is not cut off by a job timeout.
WORST_CASE = {
    "create": (1 + 2 * (ATTEMPTS - 1), 1),
    "snapshot": (ATTEMPTS, 1),
    "finalize": (6 * ATTEMPTS, 6),
    # Dedup discovery, two owner listings, and three live identity reads may
    # retry. The completion POST is attempted once; a lost response is ambiguous.
    "completion": (6 * ATTEMPTS + 1, 6),
    "admit-manual": (3 * ATTEMPTS, 3),
}


class GhError(Exception):
    pass


def gh_api(args, body=None, *, timeout_seconds=GH_TIMEOUT_SECONDS):
    timeout = min(GH_TIMEOUT_SECONDS, timeout_seconds)
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
    corresponding comments have since been deleted. A completed current Check
    is also history when a partial rerun reuses the start job's Check ID.
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
    current = next(run for page in history for run in page["check_runs"]
                   if str(run["id"]) == str(current_check_run_id))
    validate_retained_check(current, head_sha, current_check_run_id, pr_number, merge_base_sha)
    return history, prior_finding_evidence(history, head_sha, current_check_run_id, pr_number, merge_base_sha)


def validate_retained_check(run, head_sha, check_run_id, pr_number, merge_base_sha):
    """Validate the record whose evidence finalization would replace.

    An initial in-progress Check has no evidence yet. Completed legacy evidence
    remains readable without new provenance keys, but ambiguous or foreign data
    must not be overwritten as if it established an empty finding history.
    """
    try:
        if (not isinstance(run, dict) or not positive_comment_id(run.get("id"))
                or str(run["id"]) != str(check_run_id) or run.get("name") != CHECK_NAME
                or (run.get("app") or {}).get("slug") != CHECK_APP_SLUG
                or run.get("head_sha") != head_sha
                or run.get("status") not in ("in_progress", "completed")):
            raise ValueError("invalid Check identity")
        text = (run.get("output") or {}).get("text") or ""
        if not text and run["status"] == "in_progress" and run.get("conclusion") is None:
            return
        if (run["status"] != "completed" or not isinstance(text, str) or text.count("```json\n") != 1
                or not text.endswith("\n```")):
            raise ValueError("missing or ambiguous Check evidence")
        evidence = json.loads(text.split("```json\n", 1)[1][:-4], object_pairs_hook=unique_json_object)
        if (not isinstance(evidence, dict)
                or str(evidence.get("pr_number")) != str(pr_number)
                or evidence.get("merge_base_sha") != merge_base_sha
                or not re.fullmatch(r"[0-9a-f]{40}", merge_base_sha or "")
                or evidence.get("reviewed_sha", head_sha) != head_sha):
            raise ValueError("Check evidence differs from the captured diff")
        for field in ("new_finding_comment_ids", "claude_finding_comment_ids"):
            if field in evidence and not valid_comment_id_list(evidence[field]):
                raise ValueError("invalid retained finding IDs")
        if ("same_diff_findings_recorded" in evidence
                and type(evidence["same_diff_findings_recorded"]) is not bool):
            raise ValueError("invalid retained finding presence")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise GhError("the existing Check's finding evidence cannot be safely replaced") from error


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
    inline_posted_count=None,
    inline_failure_confirmed=False,
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
    if completion_verified and completion_reason not in ("", "verified"):
        return (
            "failure", "Review completion could not be verified",
            f"Trusted completion evidence is contradictory; review completion is unknown.{kept}{notes}",
        )
    if not completion_verified:
        if completion_reason == "errored_inline_tool_result" and inline_failure_confirmed:
            return (
                "failure", "Review completion could not be verified",
                (inline_failure_explanation(inline_posted_count) + inline_failure_recovery()
                 + (" Posted findings remain recorded for this commit."
                    if new_ids and inline_posted_count is None else "") + notes),
            )
        if completion_reason == "captured_inputs_not_read":
            return (
                "failure", "Review inputs were not fully read",
                STATUS_REASONS["unread_inputs"] + f" [Recovery guidance]({UNREAD_RECOVERY}).{kept}{notes}",
            )
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


SELECTION_PREFIX = "<!-- claude-review-runtime:selection:"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = ("opus", "sonnet", "haiku")
MANUAL_COMMANDS = {"/claude-review": ("", "")}
for _model in MODELS:
    MANUAL_COMMANDS[f"/claude-review {_model}"] = (_model, "")
for _effort in EFFORTS:
    MANUAL_COMMANDS[f"/claude-review effort {_effort}"] = ("", _effort)
    for _model in MODELS:
        MANUAL_COMMANDS[f"/claude-review {_model} effort {_effort}"] = (_model, _effort)


def manual_selection(body):
    if not isinstance(body, str) or body.lower() not in MANUAL_COMMANDS:
        raise ValueError("unsupported manual command")
    model, effort = MANUAL_COMMANDS[body.lower()]
    return {"requested_model": model, "requested_effort": effort}


def resolve_configuration(body, model="", effort="", *, automatic=False):
    """Resolve fields separately, without interpreting repository text as arguments."""
    manual = {} if automatic else manual_selection(body)
    config = {"version": 1, "errors": []}
    for name, raw, allowed in (("model", model, MODELS), ("effort", effort, EFFORTS)):
        key = "requested_" + name
        default = raw.strip().lower() if isinstance(raw, str) else None
        value = manual.get(key) or default
        source = "manual" if manual.get(key) else "inherited" if default == "" else "repository"
        if value not in ("", *allowed):
            value = ""
            config["errors"].append(name)
        # Whitespace-only defaults are omitted as well.
        if not value and name not in config["errors"]:
            source = "inherited"
        config[key], config[name + "_source"] = value, source
    return config


def valid_configuration(config):
    if (not isinstance(config, dict) or set(config) != {
            "version", "requested_model", "requested_effort", "model_source", "effort_source", "errors"}
            or type(config["version"]) is not int or config["version"] != 1
            or not isinstance(config["errors"], list)
            or config["errors"] != [name for name in ("model", "effort") if name in config["errors"]]):
        return None
    for name, allowed in (("model", MODELS), ("effort", EFFORTS)):
        value, source = config["requested_" + name], config[name + "_source"]
        if value not in ("", *allowed) or source not in ("manual", "repository", "inherited"):
            return None
        if name in config["errors"]:
            if value or source != "repository":
                return None
        elif bool(value) != (source != "inherited"):
            return None
    return config


def configuration_from_env(owner=None):
    if env("SELECTION_CONFIG"):
        config = valid_configuration(json.loads(env("SELECTION_CONFIG"), object_pairs_hook=unique_json_object))
        if config is None:
            raise ValueError("captured review configuration is invalid")
        return config
    return resolve_configuration(
        env("TRIGGER_COMMENT"), env("REPOSITORY_REVIEW_MODEL"), env("REPOSITORY_REVIEW_EFFORT"),
        automatic=(owner["kind"] == "automatic" if owner else
                   env("TRIGGER_KIND") == "automatic" or not env("TRIGGER_COMMENT")))


def cmd_selection(metadata=None):
    """Only allowlisted constants can reach the action's argument parser."""
    try:
        if metadata:
            with open(metadata, encoding="utf-8") as source:
                captured = json.load(source, object_pairs_hook=unique_json_object)
            if not isinstance(captured, dict):
                raise ValueError("captured review snapshot is invalid")
            if "selection_config" in captured:
                config = valid_configuration(captured["selection_config"])
                if config is None:
                    raise ValueError("captured review configuration is invalid")
                if (env("SELECTION_CONFIG") and configuration_from_env() != config
                        or any(key in captured and captured[key] != config[key]
                               for key in ("requested_model", "requested_effort"))):
                    raise ValueError("captured review configuration disagrees across job inputs")
            else:
                # Compatibility with the original manual snapshot format.
                config = resolve_configuration(env("TRIGGER_COMMENT"))
                if any(captured.get(k) != config[k] for k in ("requested_model", "requested_effort")):
                    raise ValueError("captured selection disagrees with the accepted request")
            if env("TRIGGER_KIND") == "automatic":
                if any(config[name + "_source"] == "manual" for name in ("model", "effort")):
                    raise ValueError("automatic configuration contains a manual override")
            else:
                manual = manual_selection(env("TRIGGER_COMMENT"))
                if manual_selection(captured.get("trigger_comment")) != manual:
                    raise ValueError("captured selection disagrees with the accepted request")
                for name in ("model", "effort"):
                    value = manual["requested_" + name]
                    if ((config[name + "_source"] == "manual") != bool(value)
                            or value and config["requested_" + name] != value):
                        raise ValueError("captured selection disagrees with the accepted request")
        else:
            config = configuration_from_env()
        for name in ("model", "effort"):
            write_output("requested_" + name, config["requested_" + name])
        write_output("selection_config", json.dumps(config, separators=(",", ":")))
        write_output("model_args", "--model " + config["requested_model"] if config["requested_model"] else "")
        write_output("effort_args", "--effort " + config["requested_effort"] if config["requested_effort"] else "")
        if config["errors"]:
            raise ValueError("invalid repository " + " and ".join(config["errors"]) + " setting; use a supported value or a manual override")
        if metadata:
            for key, field in (("ANTHROPIC_MODEL", "model"), ("CLAUDE_CODE_EFFORT_LEVEL", "effort")):
                selected = config["requested_" + field]
                if selected and env(key) not in ("", selected):
                    raise ValueError(key + " conflicts with the selected " + field)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"::error::Cannot select review configuration: {error}")
        return 1


def selection_record(owner, execution=None):
    if not owner:
        return None
    config = configuration_from_env(owner)
    requested = {key: config[key] for key in ("requested_model", "requested_effort")}
    record = {**requested, "generation": owner["generation"], "models": [],
              "execution_attempt": None, "model_evidence": "unknown", "observed_effort": None,
              "primary_model_evidence": None, "configuration": config}
    if execution:
        try:
            with open(execution, encoding="utf-8") as source:
                stream = json.load(source, object_pairs_hook=unique_json_object)
            record["primary_model_evidence"] = primary_model_evidence(stream)
            init = [m for m in stream if m.get("type") == "system" and m.get("subtype") == "init"]
            turns = [m for m in stream if m.get("type") == "assistant" and not m.get("error")]
            results = [m for m in stream if m.get("type") == "result"]
            sessions = {m.get("session_id") for m in [*init, *turns, *results]}
            models = {m.get("message", {}).get("model") for m in turns}
            usage = results[0].get("modelUsage", {}) if len(results) == 1 else {}
            if (len(init) != 1 or not turns or len(results) != 1 or len(sessions) != 1
                    or not all(isinstance(x, str) and x for x in sessions)
                    or not models or not isinstance(usage, dict) or not usage
                    or any(not isinstance(x, str) or not re.fullmatch(r"claude-[a-z0-9-]{1,100}", x)
                           for x in models | set(usage))):
                raise ValueError("unknown execution model")
            if any(not isinstance(v, dict) or not any(type(v.get(k)) is int and v[k] > 0
                   for k in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
                   for v in usage.values()):
                raise ValueError("no positive model usage")
            # Usage covers auxiliary calls too. Never hide additional models or
            # treat a pricing canonicalModel as the serving model identity.
            record["models"] = sorted(models | set(usage))
            if len(record["models"]) > 8:
                raise ValueError("too many models")
            record["model_evidence"] = "observed" if models == set(usage) else "mixed"
            record["execution_attempt"] = int(env("GITHUB_RUN_ATTEMPT"))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            record.update(models=[], execution_attempt=None, model_evidence="unknown")
    return record


def primary_model_evidence(stream):
    """Keep primary session sources separate from aggregate/auxiliary usage."""
    try:
        init = [m for m in stream if m.get("type") == "system" and m.get("subtype") == "init"
                and m.get("parent_tool_use_id") is None]
        turns = [m for m in stream if m.get("type") == "assistant"
                 and m.get("parent_tool_use_id") is None]
        results = [m for m in stream if m.get("type") == "result"
                   and m.get("parent_tool_use_id") is None]
        sessions = {m.get("session_id") for m in [*init, *turns, *results]}
        if (len(init) != 1 or not turns or len(results) != 1 or len(sessions) != 1
                or not all(isinstance(s, str) and s for s in sessions)
                or any(m.get("error") for m in turns)):
            return None
        evidence = {"initialization": init[0].get("model"),
                    "assistant": sorted({m.get("message", {}).get("model") for m in turns}),
                    "attempt": int(env("GITHUB_RUN_ATTEMPT"))}
        return evidence if valid_primary_model_evidence(evidence) else None
    except (ValueError, TypeError, AttributeError):
        return None


def valid_primary_model_evidence(evidence):
    if not isinstance(evidence, dict) or set(evidence) != {"initialization", "assistant", "attempt"}:
        return False
    models = evidence["assistant"]
    init = evidence["initialization"]
    return (type(evidence["attempt"]) is int and evidence["attempt"] > 0
            and isinstance(models, list) and 0 < len(models) <= 8
            and all(isinstance(m, str) and re.fullmatch(r"claude-[a-z0-9-]{1,100}", m)
                    for m in models + ([init] if init is not None else []))
            and models == sorted(set(models)))


def valid_selection(record, owner):
    if (not isinstance(record, dict) or not owner
            or set(record) - {"observed_effort", "primary_model_evidence", "configuration"} != {"generation", "requested_model", "requested_effort", "models", "execution_attempt", "model_evidence"}
            or record.get("observed_effort") is not None
            or record["generation"] != owner["generation"]
            or record["requested_model"] not in ("", *MODELS)
            or record["requested_effort"] not in ("", *EFFORTS)
            or record["model_evidence"] not in ("unknown", "observed", "mixed")
            or not isinstance(record["models"], list) or len(record["models"]) > 8
            or any(not isinstance(m, str) or not re.fullmatch(r"claude-[a-z0-9-]{1,100}", m) for m in record["models"])
            or record["models"] != sorted(set(record["models"]))):
        return None
    if "configuration" in record:
        config = valid_configuration(record["configuration"])
        if config is None or any(record[key] != config[key] for key in ("requested_model", "requested_effort")):
            return None
    attempt = record["execution_attempt"]
    if attempt is not None and (type(attempt) is not int or attempt <= 0):
        return None
    if bool(record["models"]) != (attempt is not None) or (record["model_evidence"] == "unknown") != (attempt is None):
        return None
    primary = record.get("primary_model_evidence")
    if primary is not None and (not valid_primary_model_evidence(primary)
                               or (attempt is not None and primary["attempt"] != attempt)):
        return None
    return record


def current_selection_from_env():
    try:
        return current_selection(validate_owner(json.loads(env("PRESENTATION_OWNER"))))
    except (ValueError, TypeError):
        return None


def current_selection(owner, comment=None):
    try:
        if env("SELECTION_EVIDENCE"):
            return valid_selection(json.loads(env("SELECTION_EVIDENCE"), object_pairs_hook=unique_json_object), owner)
        if env("SELECTION_CONFIG") or env("TRIGGER_COMMENT"):
            return selection_record(owner)
        return recorded_selection(owner, comment)
    except (ValueError, TypeError):
        pass
    return None


def recorded_selection(owner, comment):
    """Read persisted attempt evidence without consulting a newer request's env."""
    body = (comment or {}).get("body", "")
    matches = re.findall(re.escape(SELECTION_PREFIX) + r"(.*?) -->", body)
    try:
        if body.count(SELECTION_PREFIX) == 1 and len(matches) == 1:
            return valid_selection(json.loads(unquote(matches[0]), object_pairs_hook=unique_json_object), owner)
    except (ValueError, TypeError):
        pass
    return None


def primary_selection_models(record):
    primary = record.get("primary_model_evidence")
    return sorted(set(primary["assistant"] + ([primary["initialization"]]
                      if primary["initialization"] else []))) if primary else []


def model_label(model):
    match = re.fullmatch(r"claude-(opus|sonnet|haiku)-(\d+)-(\d+)", model)
    return (match[1].title() + " " + match[2] + "." + match[3]) if match else inline_code(model)


def selection_details(record):
    """Routine settings are informational; effort is never claimed as observed."""
    if not record:
        return []
    models = primary_selection_models(record)
    selected = record["requested_model"]
    model = selected.title() if selected else None
    if len(models) == 1 and (not selected or models[0].startswith("claude-" + selected + "-")):
        model = model_label(models[0])
    parts = ["**Model:** " + model] if model else []
    if record["requested_effort"]:
        parts.append("**Effort:** " + record["requested_effort"].capitalize())
    return [" · ".join(parts)] if parts else []


def selection_notice(record, *, running=False):
    """Keep meaningful primary-model and configuration warnings outside Details."""
    if not record:
        return []
    notices = []
    errors = (record.get("configuration") or {}).get("errors", [])
    if errors:
        notices.append("⚠️ **Review configuration:** Invalid " + " and ".join(errors)
                       + " setting. See the workflow logs.")
    selected = record["requested_model"]
    models = primary_selection_models(record)
    prefix = selected.title() + " selected; " if selected else ""
    if not models and selected and not running:
        value = prefix + "primary model evidence missing"
    elif len(models) > 1:
        value = prefix + "primary model evidence ambiguous (" + ", ".join(model_label(m) for m in models) + ")"
    elif models and selected and not models[0].startswith("claude-" + selected + "-"):
        value = prefix + "primary model mismatch (" + model_label(models[0]) + " observed)"
    else:
        value = None
    if value:
        notices.append("⚠️ **Model:** " + value)
    return notices


def inline_code(value):
    ticks = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    padding = " " if "`" in value else ""
    return f"{ticks}{padding}{value}{padding}{ticks}"


def commit_link(sha, repo=None):
    repo = repo or env("REPO")
    if not repo:
        return inline_code(sha[:7])
    return (f"[{inline_code(sha[:7])}]"
            f"({env('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/commit/{sha})")


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
    # Captured once at trusted start-job preparation, before model execution.
    if not owner or type(owner.get("started")) is not int or owner["started"] <= 0:
        return None
    try:
        return datetime.fromtimestamp(owner["started"] // 10**9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def review_trigger(label, owner):
    """Use the actual accepted event label; automatic ownership cannot identify it."""
    if not owner or label not in REVIEW_TRIGGERS:
        return None
    automatic = label in ("PR opened for review", "Draft marked ready")
    return label if automatic == (owner["kind"] == "automatic") else None


def attempt_trigger_marker(owner, label):
    receipt = quote(json.dumps([owner["generation"], label], separators=(",", ":")), safe="")
    return f"{ATTEMPT_TRIGGER_PREFIX}{receipt} -->"


def status_attempt_trigger(comment, owner):
    """Recover only a canonical receipt from the trusted exact attempt owner."""
    if not comment or not owner or not owned_status(comment) or status_owner(comment) != owner:
        return None
    body = comment.get("body") or ""
    values = re.findall(re.escape(ATTEMPT_TRIGGER_PREFIX) + r"(.*?) -->", body)
    if body.count(ATTEMPT_TRIGGER_PREFIX) != 1 or len(values) != 1:
        return None
    try:
        generation, label = json.loads(unquote(values[0]))
        if (generation == owner["generation"] and review_trigger(label, owner)
                and attempt_trigger_marker(owner, label) == ATTEMPT_TRIGGER_PREFIX + values[0] + " -->"):
            return label
    except (ValueError, TypeError):
        pass
    return None


REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.{1,2}$)[A-Za-z0-9_.-]+")
SHA = re.compile(r"[0-9a-f]{40}")
WORKFLOW = re.compile(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml")
FILE_LIMIT = 300  # GitHub's compare endpoint returns at most 300 changed files.


def workflow_path(value):
    """Accept a repository workflow path, optionally qualified by a run ref."""
    if not isinstance(value, str):
        return None
    path, separator, ref = value.partition("@")
    if separator and (not ref or any(c in ref for c in "\r\n\x00")):
        return None
    return path if WORKFLOW.fullmatch(path) else None


def runtime_provenance(repository, sha, path):
    path = workflow_path(path)
    if (isinstance(repository, str) and REPOSITORY.fullmatch(repository)
            and isinstance(sha, str) and SHA.fullmatch(sha) and path):
        return {"runtime_repository": repository, "runtime_sha": sha, "runtime_workflow_path": path}
    return None


def executing_runtime():
    return runtime_provenance(env("RUNTIME_REPOSITORY"), env("RUNTIME_SHA"), env("RUNTIME_WORKFLOW_PATH"))


def runtime_used_line(runtime):
    if not isinstance(runtime, dict):
        return None
    runtime = runtime_provenance(runtime.get("runtime_repository"), runtime.get("runtime_sha"),
                                 runtime.get("runtime_workflow_path"))
    if not runtime:
        return None
    repo, sha, path = (runtime[key] for key in ("runtime_repository", "runtime_sha", "runtime_workflow_path"))
    return f"**Runtime used:** [{inline_code(repo + '@' + sha[:7])}](https://github.com/{repo}/blob/{sha}/{path})"


def rendered_runtime(body):
    """Read one canonical provenance line only from an already trusted status section."""
    lines = [line for line in body.splitlines() if line.startswith("**Runtime used:**")]
    if len(lines) != 1:
        return None
    match = re.fullmatch(r"\*\*Runtime used:\*\* \[.*\]\(https://github.com/([^/]+/[^/]+)/blob/([0-9a-f]{40})/(.+)\)", lines[0])
    runtime = runtime_provenance(*match.groups()) if match else None
    return runtime if runtime_used_line(runtime) == lines[0] else None


def status_runtime(comment, owner, *, previous=False):
    if not owner or not owned_status(comment or {}) or status_owner(comment) != owner:
        return None
    sections = re.split(r"(?m)^(?:#{3,4} Previous completed review|\*\*Previous completed review\*\*)[ \t]*$",
                        comment.get("body") or "", maxsplit=1)
    return rendered_runtime(sections[1] if previous and len(sections) == 2 else "" if previous else sections[0])


def workflow_changed(owner, runtime_repository, runtime_workflow_path, api):
    """Return True, False, or None (unknown) using at most two API reads.

    ``owner`` is the trusted captured presentation owner. ``api`` accepts one
    gh-api argument list and returns UTF-8 JSON, like the publication helper's
    gh_api. No retries or pagination consume the enclosing presentation budget.
    All malformed, unavailable, or potentially truncated data stays unknown.
    Call only for incomplete attempts; unread-input evidence takes precedence.
    """
    try:
        repo, head, base = (owner[key] for key in ("repo", "head", "base"))
        run_id, attempt = (owner[key] for key in ("run", "attempt"))
        if (not isinstance(repo, str) or not REPOSITORY.fullmatch(repo)
                or not isinstance(head, str) or not SHA.fullmatch(head)
                or not isinstance(base, str) or not SHA.fullmatch(base)
                or type(run_id) is not int or run_id <= 0
                or type(attempt) is not int or attempt <= 0
                or not isinstance(runtime_repository, str)
                or not REPOSITORY.fullmatch(runtime_repository)
                or not workflow_path(runtime_workflow_path)):
            return None
        run = json.loads(api([f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}"]))
        if (not isinstance(run, dict) or type(run.get("id")) is not int
                or run["id"] != run_id or type(run.get("run_attempt")) is not int
                or run["run_attempt"] != attempt
                or not isinstance(run.get("repository"), dict)
                or run["repository"].get("full_name") != repo):
            return None
        caller = workflow_path(run.get("path"))
        if not caller:
            return None
        # referenced_workflows is run-wide, not the review invocation graph:
        # unrelated reusable lint/test jobs must never trigger this warning.
        paths = {caller}
        if runtime_repository == repo:
            paths.add(workflow_path(runtime_workflow_path))
        comparison = json.loads(api([f"repos/{repo}/compare/{base}...{head}"]))
        if not isinstance(comparison, dict):
            return None
        files = comparison.get("files")
        if not isinstance(files, list) or len(files) >= FILE_LIMIT:
            return None
        changed = set()
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                return None
            for key in ("filename", "previous_filename"):
                value = item.get(key)
                if value is not None:
                    if (not isinstance(value, str) or not value or value.startswith("/")
                            or any(part in ("", ".", "..") for part in value.split("/"))
                            or any(c in value for c in "\r\n\x00")):
                        return None
                    changed.add(value)
        return bool(paths & changed)
    except Exception:
        # This optional diagnostic must not prevent authoritative publication.
        # In particular, an API outage is not evidence of workflow rejection.
        return None


def inline_failure_explanation(posted_count):
    text = STATUS_REASONS["inline_tool_failed"]
    if posted_count is None:
        return text + " This review is incomplete."
    if posted_count == 0:
        return text + " No findings were posted by this attempt; this review is incomplete."
    findings = "1 finding was" if posted_count == 1 else f"{posted_count} findings were"
    verb = "remains" if posted_count == 1 else "remain"
    return text + f" {findings} posted and {verb} recorded; this review is incomplete."


def inline_failure_recovery(*, fresh_review_hint=True):
    hint = (" For a final candidate with no review in progress, request an approved fresh full manual review."
            if fresh_review_hint else "")
    return hint + f" [Recovery guidance]({INCOMPLETE_RECOVERY})."


def valid_inline_failure(record, owner):
    """Presentation-only receipt, bound to the finalized attempt, never a verdict."""
    if (not owner or not isinstance(record, dict)
            or set(record) != {"generation", "check_run_id", "posted_count"}
            or record["generation"] != owner["generation"]
            or type(record["check_run_id"]) is not int
            or not 0 < record["check_run_id"] <= MAX_COMMENT_ID
            or record["posted_count"] is not None and (
                type(record["posted_count"]) is not int or not 0 <= record["posted_count"] <= MAX_COMMENT_ID)):
        return None
    return record


def inline_failure_marker(record):
    return INLINE_FAILURE_PREFIX + quote(json.dumps(record, sort_keys=True, separators=(",", ":")), safe="") + " -->"


def finalization_inline_failure(current, check_run_id, head_sha, new_ids, posted_count):
    """Keep a count from this exact attempt, not current comment visibility.

    Completed Checks may be finalized again after comments were deleted. Their
    same-diff finding union cannot attribute a count to this particular attempt.
    Retain only its owner-bound count; legacy or conflicting evidence is unknown.
    """
    replay = current is not None and current.get("status") == "completed"
    try:
        owner = validate_owner(json.loads(env("PRESENTATION_OWNER"), object_pairs_hook=unique_json_object))
        if (owner["repo"] != env("REPO") or str(owner["pr"]) != env("PR_NUMBER")
                or owner["head"] != head_sha or owner["base"] != env("BASE_SHA")
                or owner["base_ref"] != env("BASE_REF")
                or str(owner["run"]) != env("GITHUB_RUN_ID")
                or str(owner["attempt"]) != env("GITHUB_RUN_ATTEMPT")):
            raise ValueError("owner differs from the captured attempt")
        record = valid_inline_failure({"generation": owner["generation"],
                                       "check_run_id": int(check_run_id), "posted_count": posted_count}, owner)
        if not record:
            raise ValueError("invalid count receipt")
        if replay:
            previous = recorded_evidence(current)
            retained = valid_inline_failure(previous.get("inline_failure"), owner)
            previous_ids = previous.get("new_finding_comment_ids", [])
            if (not retained or retained["check_run_id"] != record["check_run_id"]
                    or current.get("conclusion") != "failure"
                    or previous.get("completion_verified") is not False
                    or previous.get("completion_reason") != "errored_inline_tool_result"
                    or any(previous.get(key) != "success" for key in (
                        "start_check_result", "review_job_result", "action_conclusion"))
                    or any(previous.get(key) != value for key, value in review_identity(head_sha).items())
                    or not valid_comment_id_list(previous_ids)
                    or retained["posted_count"] is not None and len(previous_ids) > retained["posted_count"]
                    or not set(new_ids).issubset(previous_ids)):
                record["posted_count"] = None
            else:
                record["posted_count"] = retained["posted_count"]
        return record["posted_count"], record
    except (ValueError, TypeError, KeyError):
        return (None if replay else posted_count), None


def status_inline_failure(comment, owner):
    reason = terminal_status_reason(comment, owner)
    body = (comment or {}).get("body") or ""
    states = re.findall(re.escape(STATUS_STATE_PREFIX) + r"(.*?) -->", body)
    if body.count(STATUS_STATE_PREFIX) != 1:
        return None
    if not ((states == ["failure"] and reason == STATUS_REASONS["inline_tool_failed"])
            or (states == ["stale"] and reason == stale_status_reason(STATUS_REASONS["inline_tool_failed"]))):
        return None
    receipts = re.findall(re.escape(INLINE_FAILURE_PREFIX) + r"(.*?) -->", body)
    if body.count(INLINE_FAILURE_PREFIX) != 1 or len(receipts) != 1:
        return None
    try:
        record = valid_inline_failure(json.loads(unquote(receipts[0]), object_pairs_hook=unique_json_object), owner)
        if record and inline_failure_marker(record) == INLINE_FAILURE_PREFIX + receipts[0] + " -->":
            return record
    except (ValueError, TypeError):
        pass
    return None


def incomplete_explanation(reason, owner):
    """Render fixed, owner-bound diagnostics; never infer a provider rejection."""
    stale = reason and reason.startswith(STATUS_REASONS["stale"] + " ")
    if stale:
        reason = reason[len(STATUS_REASONS["stale"]) + 1:]
    if reason == STATUS_REASONS["unread_inputs"]:
        return reason + f" [Recovery guidance]({UNREAD_RECOVERY})."
    if reason == STATUS_REASONS["workflow_changed"] and owner:
        run = f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{owner['repo']}/actions/runs/{owner['run']}/attempts/{owner['attempt']}"
        manual = "Retry with `/claude-review`. " if not stale else ""
        return ("⚠️ The captured patch changed the review workflow, which likely caused this incomplete review. "
                f"[Check this attempt’s logs]({run}) to confirm. "
                f"{manual}See [recovery guidance]({INCOMPLETE_RECOVERY}).")
    if reason not in STATUS_REASONS.values() or reason == STATUS_REASONS["workflow_changed"]:
        reason = STATUS_REASONS["incomplete"]
    return reason + f" [Troubleshoot incomplete reviews]({INCOMPLETE_RECOVERY})."


def incomplete_status_reason(owner):
    """Use verifier outputs first; changed paths supply context, never a cause."""
    verdict = env("COMPLETION_REASON")
    if verdict in ("captured_inputs_not_read", "errored_inline_tool_result"):
        if (env("COMPLETION_VERIFIED") == "false" and env("START_RESULT", "success") == "success"
                and env("REVIEW_RESULT") == "success" and env("ACTION_CONCLUSION") == "success"):
            return STATUS_REASONS["unread_inputs" if verdict == "captured_inputs_not_read" else "inline_tool_failed"]
        # Conflicting execution evidence is unknown, not a pre-start rejection.
        return STATUS_REASONS["incomplete"]
    if env("COMPLETION_VERIFIED") == "true" or verdict not in ("", "missing_execution_file"):
        return STATUS_REASONS["incomplete"]
    # Optional context gets at most ten seconds of the shared presentation
    # budget, with no retries; reserve time for ownership checks and publication.
    changed = workflow_changed(owner, env("RUNTIME_REPOSITORY"), env("RUNTIME_WORKFLOW_PATH"),
                               lambda args: gh_api(args, timeout_seconds=5))
    return STATUS_REASONS["workflow_changed"] if changed is True else STATUS_REASONS["incomplete"]


def status_comment_body(head_sha, base_ref, state, *, check_available=True, last_review=None,
                        last_review_details=None, base_sha=None, trigger_label=None,
                        owner=None, owner_running=None, reason=None, completed_at=None,
                        completed_owner_verified=False, attempt_trigger=_TRIGGER_FROM_LABEL, selection=None,
                        runtime=None, inline_failure=None):
    """Render trusted informational state without affecting Check authority."""
    messages = {
        "in_progress": "Claude is reviewing this commit.",
        "action_required": "",
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
    selection = valid_selection(selection, owner)
    if state in ("success", "action_required"):
        last_review = (head_sha, base_ref, state)
        last_review_details = {"base_sha": env("BASE_SHA"), "run_url": env("DETAILS_URL"),
                               "completed_at": completed_at,
                               "trigger": review_trigger(trigger_label, owner),
                               "generation": owner["generation"] if owner else None, "runtime": runtime,
                               "selection": selection}
    last_review_details = last_review_details or {}
    inline_reason = reason in (STATUS_REASONS["inline_tool_failed"],
                               stale_status_reason(STATUS_REASONS["inline_tool_failed"]))
    inline_failure = (valid_inline_failure(inline_failure, owner)
                      if inline_reason and state in ("failure", "stale") and not owner_running else None)
    if inline_reason and not inline_failure:
        reason = (stale_status_reason(STATUS_REASONS["incomplete"]) if state == "stale"
                  else STATUS_REASONS["incomplete"])
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
    superseded_review = (
        state == "stale" and check_available and completed_owner_verified
        and last_review and last_review[:2] != (head_sha, base_ref)
        and not owner_running and reason == STATUS_REASONS["stale"]
    )
    if base_advanced:
        messages["stale"] = ""
        reason = STATUS_REASONS["base_advanced"]
    elif superseded_review:
        if last_review[0] != head_sha:
            warning_repo = owner["repo"] if owner else env("REPO")
            messages["stale"] = (
                f"⚠️ This review covers commit {commit_link(last_review[0], warning_repo)}. "
                f"The current PR commit {commit_link(head_sha, warning_repo)} has not been reviewed by it."
            )
        else:
            messages["stale"] = (
                f"⚠️ This review was against {inline_code(last_review[1])}. "
                f"It does not cover the PR’s current target {inline_code(base_ref)}."
            )
    selection = valid_selection(selection, owner)
    lines = [
        STATUS_MARKER,
        f"{STATUS_HEAD_PREFIX}{head_sha} -->",
        f"{STATUS_BASE_REF_PREFIX}{quote(base_ref, safe='')} -->",
        f"{STATUS_STATE_PREFIX}{state} -->",
    ]
    if inline_failure:
        lines.append(inline_failure_marker(inline_failure))
    if selection:
        lines.append(SELECTION_PREFIX + quote(json.dumps(selection, sort_keys=True, separators=(",", ":")), safe="") + " -->")
    if attempt_trigger is _TRIGGER_FROM_LABEL:
        attempt_trigger = review_trigger(trigger_label, owner) if state != "stale" else None
    else:
        # Explicit None preserves missing persisted evidence on reconciliation.
        attempt_trigger = review_trigger(attempt_trigger, owner)
    if owner:
        lines.append(owner_marker(owner))
        if attempt_trigger:
            lines.append(attempt_trigger_marker(owner, attempt_trigger))
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
        if last_review_details.get("trigger") in REVIEW_TRIGGERS and last_review_details.get("generation"):
            lines.append(f"{STATUS_REVIEWED_TRIGGER_PREFIX}{quote(last_review_details['trigger'], safe='')} -->")
    completed = valid_review_timestamp(last_review_details.get("completed_at")) if last_review else None
    if last_review and completed:
        lines.append(f"{STATUS_REVIEWED_COMPLETED_PREFIX}{completed} -->")
    if last_review:
        generation = last_review_details.get("generation")
        if isinstance(generation, str) and re.fullmatch(r"[0-9a-f]{64}", generation):
            lines.append(f"{STATUS_REVIEWED_GENERATION_PREFIX}{generation} -->")
            historical_selection = valid_selection(last_review_details.get("selection"), {"generation": generation})
            if historical_selection:
                lines.append(STATUS_REVIEWED_SELECTION_PREFIX + quote(json.dumps(
                    historical_selection, sort_keys=True, separators=(",", ":")), safe="") + " -->")
    started = owner_start_time(owner)
    active = state == "in_progress" or (state == "stale" and owner_running)
    incomplete = state in ("failure", "publication_incomplete") or (
        state == "stale" and reason
        and reason not in (STATUS_REASONS["stale"], STATUS_REASONS["base_advanced"])
    )
    started_suffix = " started " + relative_time(started) if active and started else ""
    adverse = state in ("in_progress", "failure", "publication_incomplete")
    current_attempt = active or incomplete or adverse
    show_review = last_review is not None and not current_attempt
    lines += ["## Claude Review", ""]
    specific_reason = reason and reason not in (STATUS_REASONS["stale"], STATUS_REASONS["base_advanced"])
    if state == "stale" and owner_running:
        repo = owner["repo"] if owner else None
        explanation = f"Claude is reviewing captured commit {commit_link(owner['head'] if owner else head_sha, repo)}. "
        if owner and owner["head"] != head_sha:
            explanation += f"The current PR commit {commit_link(head_sha, repo)} is not covered by this attempt."
        elif owner and owner["base_ref"] != base_ref:
            explanation += (f"This attempt targets {inline_code(owner['base_ref'])}; "
                            f"the PR now targets {inline_code(base_ref)}.")
        elif owner and isinstance(base_sha, str) and re.fullmatch(r"[0-9a-f]{40}", base_sha) and owner["base"] != base_sha:
            explanation += (f"Its captured baseline is {commit_link(owner['base'], repo)} on {inline_code(base_ref)}; "
                            f"the current target commit is {commit_link(base_sha, repo)}.")
        else:
            explanation += "This attempt's coverage of the current PR cannot be confirmed."
        lines += [explanation, ""]
    elif (state == "stale" and owner and owner["head"] != head_sha
          and reason == stale_status_reason(STATUS_REASONS["workflow_changed"])):
        messages["stale"] = (f"The review of {commit_link(owner['head'], owner['repo'])} did not complete. "
                             f"The PR is now at {commit_link(head_sha, owner['repo'])}, which this attempt hasn’t reviewed.")
    elif state == "stale" and show_review and not (base_advanced or superseded_review):
        messages["stale"] = "The applicability of this completed review to the current PR cannot be confirmed."
    if state == "in_progress" and not check_available:
        lines += [messages[state], ""]
    if show_review:
        if state == "stale" and not (base_advanced or superseded_review):
            lines += ["**Last completed review**", ""]
        row_status = "✅ **Completed**" if last_review[2] == "success" else "⚠️ **Findings**"
        if completed:
            row_status += " " + relative_time(completed)
        row_commit = last_review[0]
        row_trigger = last_review_details.get("trigger")
        if row_trigger not in REVIEW_TRIGGERS:
            row_trigger = "Not recorded"
    else:
        row_status = "🔄 **Running**" if active else "❌ **Incomplete**" if incomplete else "**Not reviewed**"
        row_status += started_suffix
        row_commit = owner["head"] if owner else head_sha
        # Refresh labels and completed-history triggers cannot identify this attempt.
        row_trigger = attempt_trigger or "Not recorded"
    repo = owner["repo"] if owner else env("REPO")
    commit_label = "Reviewed commit" if show_review else "Attempt commit" if owner else "PR commit"
    lines += [f"| Status | {commit_label} | Review trigger |", "| --- | --- | --- |",
              f"| {row_status} | {commit_link(row_commit, repo)} | {row_trigger} |"]
    notice = selection_notice(selection, running=state == "in_progress" or owner_running is True)
    lines += ["", *notice] if notice else []
    if state != "success" and not adverse and not (state == "stale" and owner_running) and messages[state]:
        lines += ["", messages[state]]
    if base_advanced:
        lines += ["", TARGET_TIP_WARNING]
    if incomplete and not active:
        if inline_failure:
            check_url = (f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{owner['repo']}/pull/{owner['pr']}"
                         f"/checks?check_run_id={inline_failure['check_run_id']}")
            explanation = (inline_failure_explanation(inline_failure["posted_count"])
                           + f" [Attempt Check]({check_url})."
                           + inline_failure_recovery(fresh_review_hint=state == "failure"))
        elif state == "publication_incomplete" or state == "failure" and not specific_reason:
            explanation = messages[state] + f" [Troubleshoot incomplete reviews]({INCOMPLETE_RECOVERY})."
        else:
            explanation = incomplete_explanation(reason, owner)
        lines += ["", explanation]
    attempt_url = (env("DETAILS_URL") if not owner else
                   f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{owner['repo']}/actions/runs/{owner['run']}")
    historical = last_review is not None and state not in ("success", "action_required")
    reviewed_url = last_review_details.get("run_url") if historical else None
    separate_attempt = historical and (
        attempt_url != reviewed_url
        or (owner and last_review_details.get("generation")
            and last_review_details["generation"] != owner["generation"])
    )
    history_in_details = last_review is not None and current_attempt
    lines += ["", "<details>", "<summary>ℹ️ Details</summary>", ""]
    if reason:
        # Existing parsers recover this exact receipt from the raw Markdown.
        # Do not add hard-break whitespace to the Reason line.
        hide_receipt = base_advanced or superseded_review or (state == "stale" and owner_running and reason == STATUS_REASONS["stale"])
        if incomplete and not active:
            diagnostic = reason.removeprefix(STATUS_REASONS["stale"] + " ")
            hide_receipt = hide_receipt or diagnostic in explanation or (
                diagnostic == STATUS_REASONS["workflow_changed"] and owner is not None) or (
                state == "publication_incomplete" and diagnostic == STATUS_REASONS["check_publication_failed"])
        if hide_receipt:
            lines += ["<!--"]
        lines += [f"**Reason:** {reason}"]
        if hide_receipt:
            lines += ["-->"]
        lines += [""]
    metadata = []
    row_ref = last_review[1] if show_review else owner["base_ref"] if owner else base_ref
    if (head_sha, base_ref) != (row_commit, row_ref):
        coverage = ""
        if state == "stale":
            coverage = " — not reviewed against this target" if row_ref != base_ref else " — not reviewed"
        metadata += [f"**PR commit:** {commit_link(head_sha, repo)} on {inline_code(base_ref)}" +
                     coverage]
    elif not show_review and not owner:
        metadata += [f"**PR target:** {inline_code(base_ref)}" +
                     (" — not reviewed" if state == "stale" else "")]
    if started and not active and (incomplete or separate_attempt):
        label = "Attempt started" if separate_attempt else "Started"
        metadata += [f"**{label}:** {relative_time(started)}"]
    reviewed_baseline = (last_review_details.get("base_sha"), last_review[1]) if last_review else None
    displayed_baselines = set()
    if owner:
        if owner["head"] not in (head_sha, row_commit):
            metadata += [f"**Attempt commit:** {commit_link(owner['head'], repo)}"]
        attempt_baseline = (owner["base"], owner["base_ref"])
        if attempt_baseline != reviewed_baseline:
            metadata += [f"**Attempt baseline:** {commit_link(owner['base'], repo)} on {inline_code(owner['base_ref'])}"]
            displayed_baselines.add(attempt_baseline)
    previous = []
    if history_in_details:
        previous += ["**Result:** " + ("No findings." if last_review[2] == "success" else "Findings."),
                     f"**Reviewed commit:** {commit_link(last_review[0], repo)} on {inline_code(last_review[1])}"]
        if completed:
            previous += [f"**Completed:** {relative_time(completed)}"]
        if last_review_details.get("trigger") in REVIEW_TRIGGERS:
            previous += [f"**Review trigger:** {last_review_details['trigger']}"]
    review_metadata = previous if history_in_details else metadata
    if last_review and last_review_details.get("base_sha"):
        review_metadata += [f"**Reviewed against:** {commit_link(last_review_details['base_sha'], repo)} "
                            f"on {inline_code(last_review[1])}"]
        displayed_baselines.add(reviewed_baseline)
    elif last_review:
        review_metadata += [f"**Reviewed target:** {inline_code(last_review[1])}"]
    if state == "stale" and base_sha and (base_sha, base_ref) not in displayed_baselines:
        metadata += [f"**Current target commit:** {commit_link(base_sha, repo)} on {inline_code(base_ref)}"]
    review_url = reviewed_url if historical else attempt_url if last_review else None
    if review_url:
        for index, field in enumerate(review_metadata):
            if field.startswith("**Reviewed against:**") or field.startswith("**Reviewed target:**"):
                review_metadata[index] = field.replace("**Reviewed ", f"**[Reviewed]({review_url}) ", 1)
                break
    attempt_link = attempt_url if not last_review or history_in_details or separate_attempt else None
    if attempt_link:
        for label in ("Attempt baseline", "Attempt started", "Started", "Attempt commit"):
            prefix = f"**{label}:**"
            index = next((i for i, field in enumerate(metadata) if field.startswith(prefix)), None)
            if index is not None:
                word = label.split()[0]
                metadata[index] = metadata[index].replace(word, f"[{word}]({attempt_link})", 1)
                break
        else:
            if not show_review:
                # Shared-baseline active attempts already show timing/status in
                # the table; link that status without adding duplicate metadata.
                label = "Running" if active else "Incomplete" if incomplete else "Not reviewed"
                linked_status = row_status.replace(f"**{label}**", f"**[{label}]({attempt_link})**", 1)
                lines = [line.replace(f"| {row_status} |", f"| {linked_status} |", 1) for line in lines]
            else:
                # Legacy history may retain a different attempt URL but no
                # trusted attempt timing or baseline. Do not invent either.
                metadata.insert(0, f"**[Attempt]({attempt_link}) status:** Not recorded")
    if not show_review and runtime_used_line(runtime):
        metadata.append(runtime_used_line(runtime))
    if not show_review:
        metadata.extend(selection_details(selection))
    if last_review and runtime_used_line(last_review_details.get("runtime")):
        review_metadata.append(runtime_used_line(last_review_details["runtime"]))
    if last_review:
        historical_selection = valid_selection(last_review_details.get("selection"),
                                               {"generation": last_review_details.get("generation")})
        review_metadata.extend(selection_details(historical_selection))
    if metadata:
        if history_in_details:
            lines += ["### Current attempt"]
        lines += ["  \n".join(metadata), ""]
    if previous:
        lines += ["### Previous completed review", "  \n".join(previous), ""]
    lines += ["</details>"]
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
    triggers = re.findall(re.escape(STATUS_REVIEWED_TRIGGER_PREFIX) + r"(.*?) -->", body)
    owner = status_owner(comment)
    historical_fields = (STATUS_REVIEWED_HEAD_PREFIX, STATUS_REVIEWED_BASE_REF_PREFIX,
                         STATUS_REVIEWED_RESULT_PREFIX, STATUS_REVIEWED_BASE_SHA_PREFIX,
                         STATUS_REVIEWED_RUN_URL_PREFIX)
    if (owned_status(comment or {}) and owner and details.get("generation")
            and details.get("base_sha") and last_completed_review(comment)
            and all(len(re.findall(re.escape(prefix) + r"(.*?) -->", body)) == 1 for prefix in historical_fields)
            and re.fullmatch(re.escape(f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{owner['repo']}/actions/runs/")
                             + r"[1-9][0-9]*", details.get("run_url", ""))
            and len(triggers) == 1 and unquote(triggers[0]) in REVIEW_TRIGGERS
            and quote(unquote(triggers[0]), safe="") == triggers[0]):
        details["trigger"] = unquote(triggers[0])
    if not values and not generations and owned_status(comment or {}):
        # A legacy terminal result proves which owner completed, even without a
        # completion date. Carry that provenance through later stale refreshes.
        owner = status_owner(comment)
        state = status_state(comment)
        if (owner and state in ("success", "action_required")
                and not status_owner_running(comment)
                and last_completed_review(comment) == (owner["head"], owner["base_ref"], state)):
            details["generation"] = owner["generation"]
    if owned_status(comment or {}) and owner and details.get("generation"):
        runtime = status_runtime(comment, owner, previous=details["generation"] != owner["generation"])
        if runtime:
            details["runtime"] = runtime
        selections = re.findall(re.escape(STATUS_REVIEWED_SELECTION_PREFIX) + r"(.*?) -->", body)
        try:
            if body.count(STATUS_REVIEWED_SELECTION_PREFIX) == 1 and len(selections) == 1:
                raw = selections[0]
                record = json.loads(unquote(raw), object_pairs_hook=unique_json_object)
                record = valid_selection(record, {"generation": details["generation"]})
                if record and quote(json.dumps(record, sort_keys=True, separators=(",", ":")), safe="") == raw:
                    details["selection"] = record
            elif (not body.count(STATUS_REVIEWED_SELECTION_PREFIX)
                  and details["generation"] == owner["generation"]
                  and not status_owner_running(comment)
                  and (status_state(comment) in ("success", "action_required")
                       or status_state(comment) == "stale" and terminal_status_reason(comment, owner) in (
                           STATUS_REASONS["stale"], STATUS_REASONS["base_advanced"]))):
                # Old completed statuses carried their selection only in the
                # current receipt. Never use it for another generation.
                record = recorded_selection(owner, comment)
                if record:
                    details["selection"] = record
        except (ValueError, TypeError):
            pass
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
    if reason in (STATUS_REASONS["base_advanced"], LEGACY_BASE_ADVANCED_REASON):
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
    if reasons[0] == LEGACY_BASE_ADVANCED_REASON:
        return STATUS_REASONS["base_advanced"]
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
    if env("PUSH_REF") and (pr.get("state") != "open" or base_ref != env("PUSH_REF")):
        raise ValueError("the PR is closed or no longer targets the pushed branch")
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


def live_pr_identity(repo, pr_number, *, include_base_sha=False, require_projection_match=False,
                     allow_projection_change=False):
    before = live_pr_projection(repo, pr_number)
    head_sha, base_ref, _ = before
    if include_base_sha:
        base_sha = direct_base_tip(repo, base_ref)
        after = live_pr_projection(repo, pr_number)
        if (after[:2] != before[:2] if allow_projection_change else after != before):
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
        if (not number.isdigit() or int(number) <= 0 or not captured[1] or len(captured[1]) > 255
                or any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (captured[0], captured[2]))):
            raise ValueError("the captured manual identity is invalid")
        projected = live_pr_projection(repo, number)
        if projected[:2] != captured[:2]:
            raise ValueError("the PR no longer matches the captured manual snapshot")
        direct_base_tip(repo, projected[1])
        if live_pr_projection(repo, number)[:2] != projected[:2]:
            raise ValueError("the PR changed during manual admission")
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
                       check_completed_at=False, inline_failure=None):
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
        def live_identity(*, require_projection_match=False):
            identity = live_pr_identity(repo, pr_number, include_base_sha=True,
                                        require_projection_match=require_projection_match,
                                        allow_projection_change=owner["kind"] != "automatic")
            if env("PUSH_REF") and identity[2] != env("PUSH_SHA"):
                raise ValueError("the target push was superseded")
            return identity
        if stale:
            if not prior or (prior["repo"], str(prior["pr"])) != (repo, pr_number):
                raise ValueError("stale refresh has no verified owner")
            owner = prior
            expected_patch = (head_sha, base_ref)
            live = live_identity()
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
        runtime = status_runtime(existing, owner) if stale else executing_runtime()
        # A refresh has no execution verdict of its own. Preserve only this
        # owner's allowlisted terminal reason; acquisition starts a new lifecycle.
        reason = terminal_status_reason(existing, owner) if stale else None
        if stale:
            inline_failure = status_inline_failure(existing, owner)
            reason = stale_status_reason(reason)
        elif inline_failure and not acquire:
            inline_failure = valid_inline_failure({**inline_failure, "generation": owner["generation"]}, owner)
        else:
            inline_failure = None
        if state == "failure":
            if env("REVIEW_RESULT") == "cancelled":
                reason = STATUS_REASONS["cancelled"]
            elif acquire or env("START_RESULT", "success") != "success":
                reason = STATUS_REASONS["preparation_failed"]
            else:
                reason = incomplete_status_reason(owner)
        elif state == "publication_incomplete":
            reason = STATUS_REASONS["check_publication_failed"]
        manual = owner["kind"] != "automatic"
        # Only a trusted terminal verdict may create a manual completion signal.
        # Refresh has no execution result and keeps its no-recreate semantics.
        manual_clean_finalize = manual and not stale and not acquire and state == "success" and check_available
        def follow_movement(live):
            return acquire or prior == owner and (
                status_owner_running(existing)
                or manual and live[:2] == (owner["head"], owner["base_ref"])
            )
        projection_must_match = not stale and not manual
        current = lambda: live_identity(require_projection_match=projection_must_match) == expected_patch
        last_review = last_completed_review(existing) if existing else None
        last_review_details = last_completed_review_details(existing)
        completed_owner_verified = (
            state in ("success", "action_required") and check_available
            or stale and completed_review_for_owner(existing, owner)
        )
        attempt_trigger = (review_trigger(env("TRIGGER_LABEL"), owner) if acquire
                           else status_attempt_trigger(existing, owner))
        trigger_label = attempt_trigger
        if (stale and not completed_owner_verified and not status_owner_running(existing)
                and last_review_details.get("generation") == owner["generation"]
                and reason == STATUS_REASONS["stale"]):
            reason = stale_status_reason(STATUS_REASONS["completion_metadata_unverified"])
        if state in ("success", "action_required") and check_available:
            # A terminal owner can still carry a previous review's history after
            # failure. Reuse a timestamp only when its completed generation matches.
            same_completion = last_review_details.get("generation") == owner["generation"]
            if (prior == owner and not status_owner_running(existing)
                    and last_review and last_review[:2] == (head_sha, base_ref)
                    and same_completion):
                # Outcome reconciliation does not change the accepted event.
                trigger_label = last_review_details.get("trigger")
            if (prior == owner and not status_owner_running(existing)
                    and last_review == (head_sha, base_ref, state)
                    and same_completion):
                stored_time = last_review_details.get("completed_at")
                if not check_completed_at or stored_time is None:
                    completed_at = stored_time
        live = live_identity()
        if (live != expected_patch and not stale
                and follow_movement(live)):
            # The Check and owner retain their captured patch. Only the visible
            # current identity follows movement; it must never imply approval.
            # The newer-owner gate above still prevents old starts/finalizers
            # from replacing another accepted attempt's presentation.
            if state in ("success", "action_required") and check_available:
                last_review = (head_sha, base_ref, state)
                last_review_details = {"base_sha": owner["base"], "run_url": env("DETAILS_URL"),
                                       "completed_at": completed_at, "generation": owner["generation"],
                                       "trigger": review_trigger(trigger_label, owner), "runtime": runtime,
                                       "selection": current_selection(owner, existing)}
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
        publication_patch = live_identity()
        if publication_patch == expected_patch and projection_must_match and not current():
            raise ValueError("captured patch authority changed before status publication")
        if publication_patch != expected_patch:
            if not stale and follow_movement(publication_patch):
                if state in ("success", "action_required") and check_available:
                    last_review = (owner["head"], owner["base_ref"], state)
                    last_review_details = {"base_sha": owner["base"], "run_url": env("DETAILS_URL"),
                                           "completed_at": completed_at, "generation": owner["generation"],
                                           "trigger": review_trigger(trigger_label, owner), "runtime": runtime,
                                           "selection": current_selection(owner, existing)}
                state = "stale"
                reason = stale_status_reason(reason)
                head_sha, base_ref = publication_patch[:2]
                expected_patch = publication_patch
                projection_must_match = False
                if (live_identity()[:2] != expected_patch[:2] if manual else not current()):
                    raise ValueError("PR identity kept changing before status publication")
            else:
                if acquire:
                    write_output("presentation_outcome", "suppressed_patch_changed")
                print("::notice::Claude Review presentation suppressed: captured head/base changed before status publication.")
                return
        body = {"body": status_comment_body(
            head_sha, base_ref, state, check_available=check_available,
            last_review=last_review, last_review_details=last_review_details, base_sha=expected_patch[2],
            trigger_label=trigger_label, owner=owner,
            owner_running=status_owner_running(existing) if stale else acquiring_running if state == "stale" else None,
            reason=reason, completed_at=completed_at,
            completed_owner_verified=completed_owner_verified, attempt_trigger=attempt_trigger,
            selection=current_selection(owner, existing), runtime=runtime, inline_failure=inline_failure)}
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
        manual_clean_finalize = (
            manual_clean_finalize and published and completed_review_for_owner(confirmed, owner)
            and last_completed_review(confirmed) == (owner["head"], owner["base_ref"], "success")
        )
        def signal_current():
            if manual_clean_finalize:
                return live_identity()[:2] == (owner["head"], owner["base_ref"])
            return current()
        def guard(require_patch=True):
            return ((not require_patch or signal_current())
                    and status_publication_identity(find_status_comment(repo, pr_number)) ==
                    status_publication_identity(confirmed))
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
                if manual_clean_finalize:
                    # A changed head/ref blocks new completion signals, but
                    # the still-owned attempt must also clear its progress.
                    require_patch = signal_current()
                    if guard(require_patch=require_patch):
                        reconcile_reactions(path, desired,
                                            lambda: guard(require_patch=require_patch),
                                            create=create and require_patch)
                elif opening:
                    if guard():
                        reconcile_reactions(path, desired, lambda: guard(), create=create)
                elif guard(require_patch=False):
                    trigger_desired = desired if signal_current() else None
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


def completion_comment_body(repo, head_sha, base_ref, base_sha, marker, details_url, *, observed_base_sha=None,
                            selection=None):
    # A fixed receipt describes this captured review; live drift belongs in the aggregate status.
    reviewed = f"[Reviewed]({details_url})" if details_url else "Reviewed"
    notice = selection_notice(selection)
    settings = "\n\n" + "\n".join(notice) if notice else ""
    metadata = [f"**{reviewed} against:** {commit_link(base_sha, repo)} on {inline_code(base_ref)}"]
    observed_tip = ""
    if observed_base_sha and observed_base_sha != base_sha:
        metadata.append(f"**Observed target commit:** {commit_link(observed_base_sha, repo)} on {inline_code(base_ref)}")
        observed_tip = "\n\nThe observed target differed from the reviewed baseline."
    runtime_line = runtime_used_line(executing_runtime())
    if runtime_line:
        metadata.append(runtime_line)
    metadata.extend(selection_details(selection))
    details = "  \n".join(metadata) + observed_tip
    return (
        f"{marker}\n"
        "Claude Review: No findings. 🎉\n\n"
        f"**Reviewed commit:** [`{head_sha[:7]}`](https://github.com/{repo}/commit/{head_sha}){settings}\n\n"
        "<details>\n<summary>ℹ️ Details</summary>\n\n"
        f"{details}\n\n</details>"
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
        owner = validate_owner(json.loads(env("PRESENTATION_OWNER")))
        if (owner["kind"] == "automatic" or
                (owner["repo"], str(owner["pr"]), owner["head"], owner["base_ref"], owner["base"],
                 str(owner["run"]), str(owner["attempt"])) !=
                (repo, pr_number, head_sha, base_ref, base_sha, env("GITHUB_RUN_ID"), env("GITHUB_RUN_ATTEMPT"))
                or details_url != f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{owner['run']}"):
            raise ValueError("the completion owner differs from the executing manual attempt")
        existing = find_status_comment(repo, pr_number)
        if (not completed_review_for_owner(existing, owner)
                or last_completed_review(existing) != (head_sha, base_ref, "success")):
            raise ValueError("the persisted owner does not establish this clean completion")
        # Read after discovery, immediately before POST. GitHub cannot make
        # this read/write atomic, so the notice describes the captured history
        # and never claims that it covers the PR's current patch.
        live = live_pr_identity(repo, pr_number, include_base_sha=True, allow_projection_change=True)
        if live[:2] != (head_sha, base_ref):
            print("::notice::Skipping manual completion notice for a superseded review snapshot.")
            return
        if status_publication_identity(find_status_comment(repo, pr_number)) != status_publication_identity(existing):
            raise ValueError("presentation changed before manual completion publication")
        body = completion_comment_body(repo, head_sha, base_ref, base_sha, marker, details_url,
                                       observed_base_sha=live[2], selection=current_selection(owner, existing))
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
    if env("BASE_REF") and re.fullmatch(r"[0-9a-f]{40}", env("BASE_SHA")):
        lines += ["", f"**Captured baseline:** {inline_code(env('BASE_REF'))} at `{env('BASE_SHA')}`"]
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
        "base_ref": env("BASE_REF") or None,
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
        and completion_reason in ("", "verified")
    )
    merge_base_sha = env("MERGE_BASE_SHA")

    # Each source is read independently. The snapshot attributes newly posted
    # comments to this run's captured diff; trusted Check history attributes
    # preexisting comments. A raw head SHA alone cannot identify their diff.
    errors = []
    history = []
    current_check = None
    retained_check_unavailable = False
    try:
        history, (prior_ids, recorded_ids, other_diff_ids) = fetch_prior_finding_evidence(
            repo, head_sha, check_run_id, pr_number, merge_base_sha
        )
        current_check = next(run for page in history for run in page["check_runs"]
                             if str(run["id"]) == str(check_run_id))
    except GhError as error:
        prior_ids, recorded_ids, other_diff_ids = [], set(), set()
        errors.append(f"earlier Claude Review history is unavailable ({error})")
        # A history outage cannot authorize erasing this Check's old findings.
        # A direct read can still preserve them while the review remains failed.
        try:
            current = with_retries(lambda: json.loads(gh_api([
                f"repos/{repo}/check-runs/{check_run_id}"]), object_pairs_hook=unique_json_object),
                "Reading the existing Claude Review Check")
            validate_retained_check(current, head_sha, check_run_id, pr_number, merge_base_sha)
            current_check = current
            prior_ids, recorded_ids, other_diff_ids = prior_finding_evidence(
                [{"check_runs": [current]}], head_sha, check_run_id, pr_number, merge_base_sha)
        except (GhError, ValueError, TypeError, KeyError):
            retained_check_unavailable = True

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

    # Exact new-publication counts require both discovery and its pre-review
    # baseline. Missing/invalid evidence must never turn an unknown count into zero.
    inline_posted_count = len(new_ids) if before is not None and observed_ids is not None and not errors else None
    inline_failure = None
    if (start_result, review_result, action_conclusion, env("COMPLETION_VERIFIED"), completion_reason) == (
            "success", "success", "success", "false", "errored_inline_tool_result"):
        inline_posted_count, inline_failure = finalization_inline_failure(
            current_check, check_run_id, head_sha, new_ids, inline_posted_count)
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
        inline_posted_count=inline_posted_count,
        inline_failure_confirmed=env("COMPLETION_VERIFIED") == "false",
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
        "selection": current_selection_from_env(),
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
    if inline_failure:
        evidence["inline_failure"] = inline_failure
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
    if retained_check_unavailable:
        # Even the old action_required conclusion can be the only durable
        # finding receipt. Do not replace it when its evidence is unknown.
        best_effort_status(head_sha, "publication_incomplete")
        print("::error::Existing Check evidence was unavailable; the Check was not changed.")
        return 1
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
                                             check_completed_at=True,
                                             inline_failure=inline_failure)
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


# GitHub matrices allow 256 jobs. Refuse overflow rather than silently omit PRs.
TARGET_PR_LIMIT = 256
TARGET_PAGE_LIMIT = 100


def target_push_is_current():
    ref, sha = env("PUSH_REF"), env("PUSH_SHA")
    if (not ref or len(ref) > 255 or not re.fullmatch(r"[0-9a-f]{40}", sha)
            or sha == "0" * 40):
        raise ValueError("a branch push ref and non-deletion SHA are required")
    return direct_base_tip(env("REPO"), ref) == sha


def cmd_discover_target():
    """Read-only, bounded discovery; never emit a partial matrix on failure."""
    global PRESENTATION_DEADLINE
    PRESENTATION_DEADLINE = time.monotonic() + PRESENTATION_SECONDS
    try:
        if not target_push_is_current():
            write_output("prs", "[]")
            return 0
        numbers = set()
        for page in range(1, TARGET_PAGE_LIMIT + 1):
            query = urlencode({"state": "open", "base": env("PUSH_REF"),
                               "per_page": 100, "page": page})
            raw = with_retries(lambda: gh_api([f"repos/{env('REPO')}/pulls?{query}"]),
                               "Discovering affected open PRs")
            items = json.loads(raw, object_pairs_hook=unique_json_object)
            if not isinstance(items, list) or len(items) > 100:
                raise ValueError("invalid open PR page")
            for pr in items:
                number = pr["number"]
                if type(number) is not int or number <= 0:
                    raise ValueError("invalid PR number")
                # Do not trust the API filter alone, including prefix-like refs.
                if (pr["state"] == "open" and pr["base"]["ref"] == env("PUSH_REF")
                        and pr["base"]["repo"]["full_name"] == env("REPO")):
                    numbers.add(number)
                if len(numbers) > TARGET_PR_LIMIT:
                    raise ValueError("affected PR fan-out exceeds 256; no refresh jobs emitted")
            if len(items) < 100:
                break
        else:
            raise ValueError("open PR pagination exceeded its bound; no refresh jobs emitted")
        # Another push during discovery makes this event obsolete.
        write_output("prs", json.dumps(sorted(numbers)) if target_push_is_current() else "[]")
        return 0
    except (GhError, ValueError, KeyError, TypeError) as error:
        print(f"::error::Could not discover target-branch refreshes: {error}")
        return 1
    finally:
        PRESENTATION_DEADLINE = None


def cmd_refresh_target():
    """Resolve the queued PR live; existing ownership guards handle publication."""
    global PRESENTATION_DEADLINE
    PRESENTATION_DEADLINE = time.monotonic() + PRESENTATION_SECONDS
    try:
        number = env("PR_NUMBER")
        if not number.isdigit() or int(number) <= 0:
            raise ValueError("invalid PR number")
        if not target_push_is_current():
            print("::notice::Target push was superseded; skipping status refresh.")
            return 0
        head, base, _ = live_pr_projection(env("REPO"), number)
        os.environ.update(HEAD_SHA=head, BASE_REF=base)
    except (GhError, ValueError, KeyError, TypeError) as error:
        print(f"::warning::Could not admit target-branch refresh: {error}")
        return 0
    finally:
        PRESENTATION_DEADLINE = None
    return cmd_stale()


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
    sub.add_parser("discover-target")
    sub.add_parser("refresh-target")
    sub.add_parser("admit-manual")
    selection = sub.add_parser("selection")
    selection.add_argument("--metadata")
    observation = sub.add_parser("observe-selection")
    observation.add_argument("--execution", required=True)
    args = parser.parse_args(argv)
    if args.command == "selection":
        return cmd_selection(args.metadata)
    if args.command == "observe-selection":
        try:
            owner = validate_owner(json.loads(env("PRESENTATION_OWNER")))
            write_output("selection_evidence", json.dumps(selection_record(owner, args.execution), separators=(",", ":")))
        except (ValueError, TypeError):
            pass
        return 0
    if args.command == "create":
        return cmd_create()
    if args.command == "snapshot":
        try:
            return cmd_snapshot(args.output)
        except GhError as error:
            print(f"::error::Could not record existing Claude review comments: {error}")
            return 1
    if args.command == "discover-target":
        return cmd_discover_target()
    if args.command == "refresh-target":
        return cmd_refresh_target()
    if args.command == "stale":
        return cmd_stale()
    if args.command == "admit-manual":
        return cmd_admit_manual()
    return cmd_finalize()


if __name__ == "__main__":
    sys.exit(main())
