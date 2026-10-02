#!/usr/bin/env python3
"""Publish the trusted `Claude Review` Check Run for one reviewed PR snapshot.

Runs only in workflow jobs that never invoke Claude. The check run is the
authoritative per-commit review state; the model never writes it.

Subcommands (configured through environment variables set by the workflow):
  create    open an in_progress check run for HEAD_SHA
  snapshot  record Claude's review-comment IDs on HEAD_SHA before the review
  finalize  map the review outcome to a conclusion and complete the check run
  stale     refresh an existing status comment for a new PR head
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
STATUS_AUTHOR = "github-actions[bot]"
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
# Worst case per command as (GitHub calls, retried operations). Each call is
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
    # The initial live-head read, listing, update, and PR-reaction
    # identity recheck can each exhaust their retries. A new POST is attempted
    # once, so the update path is the larger bound. Reaction reconciliation
    # adds up to three bounded, non-retried calls.
    "status": (4 * ATTEMPTS + 3, 4),
    "stale": (4 * ATTEMPTS + 3, 4),
    # Paginated dedup discovery and the live snapshot read may retry; the
    # completion POST is attempted once because a lost response is ambiguous.
    "completion": (2 * ATTEMPTS + 1, 2),
}


class GhError(Exception):
    pass


def gh_api(args, body=None):
    try:
        proc = subprocess.run(
            ["gh", "api", *args],
            input=None if body is None else json.dumps(body),
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
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
            time.sleep(delay * 2 ** (attempt - 1))


def parse_pages(text):
    """Decode `gh api --paginate` output: one JSON document per page, concatenated."""
    decoder = json.JSONDecoder()
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


def check_run_history(repo, head_sha):
    # filter=all: the endpoint's default returns only the latest run per name.
    query = urlencode({"check_name": CHECK_NAME, "filter": "all", "per_page": 100})
    return parse_pages(gh_api(["--paginate", f"repos/{repo}/commits/{head_sha}/check-runs?{query}"]))


def fetch_prior_finding_evidence(repo, head_sha, current_check_run_id, pr_number, merge_base_sha):
    history = with_retries(
        lambda: check_run_history(repo, head_sha), "Listing Claude Review check history"
    )
    return prior_finding_evidence(history, head_sha, current_check_run_id, pr_number, merge_base_sha)


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


def status_comment_body(head_sha, base_ref, state, *, check_available=True, last_review=None,
                        trigger_label=None):
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
        "stale": "The current head or base is not covered by a completed Claude Review. Request a new review.",
    }
    if not check_available:
        messages["in_progress"] = "Claude is reviewing this commit, but authoritative Check publication is unavailable."
        messages["publication_incomplete"] = (
            "Review completion cannot be established because authoritative Check publication is unavailable. "
            "Consult the workflow run and inline review threads."
        )
    if state in ("success", "action_required"):
        last_review = (head_sha, base_ref, state)
    lines = [
        STATUS_MARKER,
        f"{STATUS_HEAD_PREFIX}{head_sha} -->",
        f"{STATUS_BASE_REF_PREFIX}{quote(base_ref, safe='')} -->",
        f"{STATUS_STATE_PREFIX}{state} -->",
    ]
    if last_review:
        reviewed_sha, reviewed_base, reviewed_result = last_review
        lines += [
            f"{STATUS_REVIEWED_HEAD_PREFIX}{reviewed_sha} -->",
            f"{STATUS_REVIEWED_BASE_REF_PREFIX}{quote(reviewed_base, safe='')} -->",
            f"{STATUS_REVIEWED_RESULT_PREFIX}{reviewed_result} -->",
        ]
    lines += [
        f"### {headings[state]}",
        "",
        f"**Current commit:** `{head_sha[:7]}`" +
        (f" on {inline_code(base_ref)} — not reviewed" if state == "stale" else ""),
    ]
    if state != "stale" and trigger_label:
        lines.append(f"**Trigger:** {trigger_label}")
    if state == "stale" and last_review:
        reviewed_sha, reviewed_base, reviewed_result = last_review
        result = "✅ clean" if reviewed_result == "success" else "⚠️ findings"
        lines.append(f"**Last reviewed:** `{reviewed_sha[:7]}` on {inline_code(reviewed_base)} — {result}")
    lines += ["", messages[state]]
    if env("DETAILS_URL"):
        lines += ["", f"[Workflow run]({env('DETAILS_URL')})"]
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


def update_status_comment(repo, pr_number, head_sha, base_ref, state, *, check_available=True, create=True):
    existing = find_status_comment(repo, pr_number)
    if (state == "stale" and existing and status_head(existing) == head_sha
            and status_base_ref(existing) == base_ref):
        # A delayed refresh must not clear an active/current result. A repeat
        # refresh of an already-stale comment may retry PR reaction cleanup.
        return existing["id"] if status_state(existing) == "stale" else False
    if not existing and not create:
        return None
    body = {"body": status_comment_body(
        head_sha, base_ref, state, check_available=check_available,
        last_review=last_completed_review(existing) if existing else None,
        trigger_label=env("TRIGGER_LABEL"),
    )}
    if existing:
        comment_id = existing["id"]
        # The emergency no-helper fallback can still repair this known comment
        # if the update itself fails after discovery.
        write_output("status_comment_id", comment_id)
        with_retries(
            lambda: gh_api(
                ["--method", "PATCH", f"repos/{repo}/issues/comments/{comment_id}", "--input", "-"], body
            ),
            "Updating Claude Review status comment",
        )
        return comment_id
    # A failed POST may have succeeded remotely. Never retry it blindly; the
    # next lifecycle step can rediscover the marker through the listing above.
    created = gh_api(
        ["--method", "POST", f"repos/{repo}/issues/{pr_number}/comments", "--input", "-"], body
    )
    comment_id = json.loads(created)["id"]
    write_output("status_comment_id", comment_id)
    return comment_id


def desired_pr_reaction(state, check_available):
    if not check_available:
        return None
    if state == "success":
        return "+1"
    if state == "in_progress" and env("PR_REACTION_MODE") in ("automatic", "manual"):
        return "eyes"
    return None


def reconcile_pr_reactions(repo, pr_number, desired):
    """Best-effort projection of Claude Review reactions on the top-level PR.

    Consumers reserve github-actions[bot] eyes/+1 on top-level PRs for
    Claude Review. GitHub returns this actor's reactions with user.type User,
    so ownership uses the reserved login/content pair. Revisit ownership
    before another workflow uses that pair.
    """
    path = f"repos/{repo}/issues/{pr_number}/reactions"
    raw = gh_api(["--paginate", f"{path}?per_page=100"])
    owned = [
        reaction
        for page in parse_pages(raw)
        for reaction in page
        if (reaction.get("user") or {}).get("login") == STATUS_AUTHOR
        and reaction.get("content") in ("eyes", "+1")
    ]
    removed = True
    kept_desired = False
    for reaction in owned:
        if reaction["content"] == desired and not kept_desired:
            kept_desired = True
            continue
        try:
            gh_api(["--method", "DELETE", f"{path}/{reaction['id']}"])
        except GhError as error:
            removed = False
            print(f"::warning::Could not remove an obsolete Claude Review reaction: {error}")
    if desired and removed and not kept_desired:
        gh_api(["--method", "POST", path, "--input", "-"], {"content": desired})


def live_pr_identity(repo, pr_number, *, include_base_sha=False):
    current = with_retries(
        lambda: gh_api([f"repos/{repo}/pulls/{pr_number}"]),
        "Reading the current PR head and base",
    )
    pr = json.loads(current)
    head_sha, base_ref = pr["head"]["sha"], pr["base"]["ref"]
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError("the live PR head SHA is invalid")
    if not isinstance(base_ref, str) or not base_ref:
        raise ValueError("the live PR base ref is invalid")
    if include_base_sha:
        base_sha = pr["base"]["sha"]
        if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
            raise ValueError("the live PR base SHA is invalid")
        return head_sha, base_ref, base_sha
    return head_sha, base_ref


def best_effort_status(head_sha, state, *, check_available=True, create=True):
    if env("STATUS_COMMENTS_ENABLED") != "true" or not env("PR_NUMBER"):
        return
    repo, pr_number = env("REPO"), env("PR_NUMBER")
    published = False
    identity_verified = state == "stale"  # cmd_stale checked the live identity.
    try:
        base_ref = env("BASE_REF")
        if not base_ref:
            raise ValueError("the reviewed PR base ref is missing")
        # The Check is always published for the captured review SHA. Only the
        # informational comment and reactions follow the current PR identity.
        if state != "stale" and live_pr_identity(repo, pr_number) != (head_sha, base_ref):
            print(f"::notice::Skipping status for superseded review identity {head_sha} on {base_ref}.")
            return
        identity_verified = True
        comment_id = update_status_comment(
            repo, pr_number, head_sha, base_ref, state,
            check_available=check_available, create=create,
        )
        if comment_id is False:
            return  # A same-identity stale event must not clear an active review.
        published = True
    except Exception as error:
        print(f"::warning::Could not publish Claude Review status comment: {error}")
    if not identity_verified:
        return
    if env("PR_REACTION_MODE") not in ("automatic", "manual", "stale"):
        return
    try:
        desired = desired_pr_reaction(state, check_available) if published else None
        # A PR reaction is shared across heads. Never add or clear one after
        # the PR moved during comment publication.
        if live_pr_identity(repo, pr_number) != (head_sha, base_ref):
            return
        reconcile_pr_reactions(repo, pr_number, desired)
    except Exception as error:
        print(f"::warning::Could not reconcile Claude Review reactions: {error}")


def completion_marker(repo, pr_number, head_sha, base_ref, base_sha, merge_base_sha):
    identity = [repo, str(pr_number), head_sha, base_ref, base_sha, merge_base_sha]
    encoded = quote(json.dumps(identity, separators=(",", ":")), safe="")
    return f"{COMPLETION_PREFIX}{encoded} -->"


def completion_comment_body(repo, head_sha, base_ref, base_sha, marker, details_url):
    return (
        f"{marker}\n"
        "🎉 Claude review completed—no findings on "
        f"[`{head_sha[:7]}`](https://github.com/{repo}/commit/{head_sha}) against "
        f"{inline_code(base_ref)} at "
        f"[`{base_sha[:7]}`](https://github.com/{repo}/commit/{base_sha}). "
        f"[Review run]({details_url})."
    )


def best_effort_manual_completion(head_sha):
    """Publish historical UX only after this run's clean Check was published."""
    if env("MANUAL_COMPLETION_ENABLED") != "true":
        return
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
        if live_pr_identity(repo, pr_number, include_base_sha=True) != (head_sha, base_ref, base_sha):
            print("::notice::Skipping manual completion notice for a superseded review snapshot.")
            return
        body = completion_comment_body(repo, head_sha, base_ref, base_sha, marker, details_url)
        # Never retry an ambiguous POST. A subsequent clean rerun can discover
        # a notice that was created despite a failed response.
        gh_api(["--method", "POST", path, "--input", "-"], {"body": body})
    except Exception as error:
        print(f"::warning::Could not publish manual Claude Review completion notice: {error}")


def summary_lines(head_sha, sentence):
    lines = [
        f"**Reviewed commit:** `{head_sha}`",
        f"**Trigger:** {env('TRIGGER_LABEL', 'Claude review')}",
        "",
        sentence,
    ]
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
            best_effort_status(head_sha, "in_progress", check_available=False)
            return 0
        print(f"::error::Could not create the Claude Review check run: {error}")
        return 1
    write_output("check_run_id", check_run_id)
    print(f"Created Claude Review check run {check_run_id} for {head_sha}")
    best_effort_status(head_sha, "in_progress")
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
    try:
        prior_ids, recorded_ids, other_diff_ids = fetch_prior_finding_evidence(
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
    body = {
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": now(),
        "output": {
            "title": title,
            "summary": summary_lines(head_sha, sentence),
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
            best_effort_status(head_sha, "failure")
        except GhError:
            print("::error::Could not finalize the Claude Review check run; it may remain in progress.")
            best_effort_status(head_sha, "publication_incomplete")
        return 1
    print(f"Claude Review check run {check_run_id}: {conclusion} ({title})")
    best_effort_status(head_sha, conclusion)
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
    best_effort_status(head_sha, "stale", create=False)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create")
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--output", required=True)
    sub.add_parser("finalize")
    sub.add_parser("stale")
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
    return cmd_finalize()


if __name__ == "__main__":
    sys.exit(main())
