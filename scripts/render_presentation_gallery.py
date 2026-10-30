#!/usr/bin/env python3
"""Render synthetic visual-QA samples offline, using the production helpers."""

import sys

# Also protect callers who omit -B: importing the fixtures/helper must not write.
sys.dont_write_bytecode = True

import argparse
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import re
from unittest import mock
from typing import NamedTuple


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "presentation_gallery_fixtures", ROOT / "tests" / "test_claude_review_check.py"
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
check = fixtures.check

HEADING = "SAMPLE ONLY — Claude Review gallery"
GALLERY = ROOT / "docs" / "review-presentation-gallery.md"
REGENERATE = "python3 -B scripts/render_presentation_gallery.py --write"
FINDINGS_SAMPLE = "current-findings"
SYNTHETIC_FINDING = (
    "`src/cache.py:42` — **[P2] Invalidate cached entries after deletion.** "
    "Deleting an item removes it from storage but leaves its cached value intact, "
    "so subsequent reads can return the deleted item until the cache expires. "
    "Invalidate the cache entry after a successful deletion."
)
COMPLETED_AT = "2026-10-07T13:09:19Z"
STARTED = 1791378000000000000  # 2026-10-07T13:00:00Z
OPERATIONAL_MARKER = re.compile(r"<!-- claude-review-runtime:[^\r\n]*? -->\n?")


class GallerySample(NamedTuple):
    case_id: str
    group: str
    title: str
    body: str


# Reading order is separate from stable case identities and display numbers.
GROUP_ORDER = (
    "current-completion", "baseline-completion", "changed-head", "incomplete",
    "compact-completion", "manual-recovery", "running-history", "incomplete-history", "retargeted",
    "publication-incomplete", "publication-unavailable", "running-unavailable",
    "shared-run", "legacy-history", "stale-running",
)


@contextmanager
def fixture_environment():
    """Prevent caller configuration or credentials from entering sample output."""
    original = os.environ.copy()
    os.environ.clear()
    os.environ.update({
        "REPO": fixtures.REPO,
        "BASE_SHA": fixtures.BASE_TIP,
        "DETAILS_URL": "https://github.com/owner/repo/actions/runs/5",
        "GITHUB_SERVER_URL": "https://github.com",
        "RUNTIME_REPOSITORY": "JBallin/claude-review-runtime",
        "RUNTIME_SHA": "f" * 40,
        "RUNTIME_WORKFLOW_PATH": ".github/workflows/claude.yml",
    })
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def strip_operational_markers(body):
    """Remove Runtime record markers only; leave hidden Reason receipts intact."""
    return OPERATIONAL_MARKER.sub("", body)


def gallery_samples():
    """Return stable cases grouped for reading, using raw production output."""
    with fixture_environment(), mock.patch.object(check, "now", return_value=COMPLETED_AT):
        owner = fixtures.presentation_owner(started=STARTED)
        details = {
            "base_sha": fixtures.BASE_TIP,
            "run_url": "https://github.com/owner/repo/actions/runs/5",
            "completed_at": COMPLETED_AT,
            "trigger": "PR opened for review",
            "generation": owner["generation"],
            "runtime": check.executing_runtime(),
        }

        def status(state, *, head=fixtures.HEAD, base_ref="main", **options):
            return check.status_comment_body(
                head, base_ref, state, **{
                    "owner": owner,
                    "trigger_label": "PR opened for review",
                    "completed_at": COMPLETED_AT,
                    "runtime": check.executing_runtime() if options.get("owner", owner) else None,
                    **options,
                }
            )

        def history(result):
            return (fixtures.HEAD, "main", result)

        stale_options = {
            "last_review_details": details,
            "owner_running": False,
            "reason": check.STATUS_REASONS["stale"],
            "completed_owner_verified": True,
        }
        manual_owner = fixtures.presentation_owner(kind="issue_comment", target=12, started=STARTED)
        def selected(models, evidence="observed", attempt=1, model="opus", effort="high", state="success", primary=None, legacy=False, receipt=False, **options):
            record = {"generation": manual_owner["generation"], "requested_model": model,
                      "requested_effort": effort, "models": models,
                      "model_evidence": evidence, "execution_attempt": attempt, "observed_effort": None}
            if not legacy:
                primary = primary if primary is not None else models
                record["primary_model_evidence"] = ({"initialization": primary[0], "assistant": primary,
                                                     "attempt": attempt} if primary else None)
            if receipt:
                return check.completion_comment_body(
                    fixtures.REPO, fixtures.HEAD, "main", fixtures.BASE_TIP,
                    check.completion_marker(fixtures.REPO, 7, fixtures.HEAD, "main",
                                            fixtures.BASE_TIP, fixtures.MERGE_BASE),
                    "https://github.com/owner/repo/actions/runs/5",
                    observed_base_sha=fixtures.BASE_TIP, selection=record)
            return check.status_comment_body(fixtures.HEAD, "main", state, owner=manual_owner,
                                            trigger_label="Manual request", completed_at=COMPLETED_AT,
                                            selection=record, runtime=check.executing_runtime(), **options)
        samples = [
            GallerySample("selection-running", "current-completion", "Running with explicit selection", selected([], "unknown", None, state="in_progress")),
            GallerySample("selection-findings", "current-completion", "Findings with explicit selection", selected(["claude-opus-5-5"], state="action_required")),
            GallerySample("selection-tooling-unavailable", "current-completion", "Review tooling unavailable with explicit selection", selected([], "unknown", None, state="failure", reason=check.STATUS_REASONS["tooling_unavailable"])),
            GallerySample("selection-incomplete", "current-completion", "Incomplete with explicit selection", selected([], "unknown", None, state="failure")),
            GallerySample("plain-selection", "current-completion", "Plain manual request", selected(["claude-sonnet-5-5"], model="", effort="")),
            GallerySample("effort-only", "current-completion", "Effort-only request", selected(["claude-sonnet-5-5"], model="")),
            GallerySample("opus-observed", "current-completion", "Opus observed with requested effort", selected(["claude-opus-5-5"])),
            GallerySample("opus-mismatch", "current-completion", "Completed review with model mismatch", selected(["claude-sonnet-5-5"])),
            GallerySample("opus-unknown", "current-completion", "Completed review with unknown model", selected([], "unknown", None)),
            GallerySample("opus-multiple", "current-completion", "Opus primary review with auxiliary Haiku usage", selected(["claude-haiku-4-5", "claude-opus-5-5"], "mixed", primary=["claude-opus-5-5"])),
            GallerySample("opus-ambiguous", "current-completion", "Completed review with conflicting primary models", selected(["claude-opus-5-5", "claude-sonnet-5-5"])),
            GallerySample("opus-legacy", "current-completion", "Legacy receipt without primary evidence", selected(["claude-opus-5-5"], legacy=True)),
            GallerySample("current-clean", "current-completion", "Completed review", status("success")),
            GallerySample(FINDINGS_SAMPLE, "current-completion", "Review with findings", status("action_required")),
            GallerySample("baseline-clean", "baseline-completion", "Clean completion, changed target branch", status(
                "stale", last_review=history("success"), base_sha=fixtures.OTHER,
                **stale_options)),
            GallerySample("baseline-findings", "baseline-completion", "Findings, changed target branch", status(
                "stale", last_review=history("action_required"), base_sha=fixtures.OTHER,
                **stale_options)),
            GallerySample("changed-head", "changed-head", "PR commit changed after clean completion", status(
                "stale", head=fixtures.OTHER, last_review=history("success"),
                base_sha=fixtures.BASE_TIP, **stale_options)),
            GallerySample("incomplete-unknown", "incomplete", "Incomplete attempt without completed history", status(
                "failure", reason=check.STATUS_REASONS["incomplete"])),
            GallerySample("selection-receipt", "compact-completion", "Historical manual receipt with primary Opus and auxiliary Haiku", selected(["claude-haiku-4-5", "claude-opus-5-5"], "mixed", primary=["claude-opus-5-5"], receipt=True)),
            GallerySample("selection-receipt-mismatch", "compact-completion", "Historical manual receipt with primary model mismatch", selected(["claude-sonnet-5-5"], receipt=True)),
            GallerySample("compact-clean", "compact-completion", "Compact clean completion, matching baseline", check.completion_comment_body(
                fixtures.REPO, fixtures.HEAD, "main", fixtures.BASE_TIP,
                check.completion_marker(fixtures.REPO, 7, fixtures.HEAD, "main",
                                        fixtures.BASE_TIP, fixtures.MERGE_BASE),
                "https://github.com/owner/repo/actions/runs/5",
                observed_base_sha=fixtures.BASE_TIP)),
            GallerySample("compact-differing-baseline", "compact-completion", "Compact clean completion, differing baseline", check.completion_comment_body(
                fixtures.REPO, fixtures.HEAD, "main", fixtures.BASE_TIP,
                check.completion_marker(fixtures.REPO, 7, fixtures.HEAD, "main",
                                        fixtures.BASE_TIP, fixtures.MERGE_BASE),
                "https://github.com/owner/repo/actions/runs/5",
                observed_base_sha=fixtures.OTHER)),
        ]
        # This fixture explicitly supplies the pin-change context. Generic manual
        # receipts know the executed Runtime, but cannot infer a proposed pin.
        pin_recovery = check.completion_comment_body(
            fixtures.REPO, fixtures.HEAD, "main", fixtures.BASE_TIP,
            check.completion_marker(fixtures.REPO, 7, fixtures.HEAD, "main",
                                    fixtures.BASE_TIP, fixtures.MERGE_BASE),
            "https://github.com/owner/repo/actions/runs/5")
        pin_context = "This review used the deployed Runtime, not the proposed pin."
        runtime_line = check.runtime_used_line(check.runtime_provenance(
            "JBallin/claude-review-runtime", "f" * 40, ".github/workflows/claude.yml"))
        samples.append(GallerySample(
            "manual-pin-recovery", "manual-recovery", "Manual recovery after a Runtime pin change (synthetic)",
            pin_recovery.replace(runtime_line, runtime_line + "  \n" + pin_context)))
        for count, label in ((1, "partial"), (0, "zero"), (None, "unknown-count")):
            samples.append(GallerySample(f"inline-tool-{label}", "incomplete",
                f"Inline-comment tool failure: {label}", status(
                    "failure", reason=check.STATUS_REASONS["inline_tool_failed"],
                    inline_failure={"generation": owner["generation"], "check_run_id": 99, "posted_count": count})))
        samples.append(GallerySample("inline-tool-stale", "incomplete",
            "Inline-comment tool failure after the PR commit changes", status(
                "stale", head=fixtures.OTHER, owner_running=False,
                reason=check.stale_status_reason(check.STATUS_REASONS["inline_tool_failed"]),
                inline_failure={"generation": owner["generation"], "check_run_id": 99, "posted_count": 1})))
        newer_owner = fixtures.presentation_owner(
            head=fixtures.OTHER, base=fixtures.NEWER, run=6,
            started=STARTED + 600_000_000_000,
        )
        for state, group, description in (
                ("in_progress", "running-history", "Running attempt with older completed history"),
                ("failure", "incomplete-history", "Incomplete attempt with older completed history")):
            for result, label in (("success", "clean"), ("action_required", "findings")):
                options = {"reason": check.STATUS_REASONS["incomplete"]} if state == "failure" else {}
                samples.append(GallerySample(f"{group}-{label}", group, f"{description}: {label}", status(
                    state, head=fixtures.OTHER, owner=newer_owner, trigger_label="Draft marked ready",
                    last_review=history(result), last_review_details=details, **options)))
        for result, label in (("success", "clean"), ("action_required", "findings")):
            samples.append(GallerySample(f"retargeted-{label}", "retargeted", f"Retargeted provenance: {label}", status(
                "stale", base_ref="release", last_review=history(result),
                base_sha=fixtures.BASE_TIP, **stale_options)))
        for group, state, available, description in (
                ("publication-incomplete", "publication_incomplete", True, "Authoritative Check publication incomplete"),
                ("publication-unavailable", "publication_incomplete", False, "Completion with unavailable Check publication"),
                ("running-unavailable", "in_progress", False, "Running with unavailable Check publication")):
            for result, label in ((None, "no completed history"),
                                  ("success", "previous clean review"),
                                  ("action_required", "previous findings")):
                options = ({"reason": check.STATUS_REASONS["check_publication_failed"]}
                           if state == "publication_incomplete" else {})
                samples.append(GallerySample(f"{group}-{result or 'none'}".replace("_", "-"), group, f"{description}: {label}", status(
                    state, head=fixtures.OTHER, owner=newer_owner,
                    trigger_label="Draft marked ready", check_available=available,
                    last_review=history(result) if result else None,
                    last_review_details=details if result else None, **options)))
        manual_owner = fixtures.presentation_owner(
            kind="issue_comment", target=11, head=fixtures.OTHER, base=fixtures.NEWER,
            run=6, started=STARTED + 600_000_000_000,
        )
        samples += [
            GallerySample("unread-automatic", "incomplete", "Required inputs unread, automatic attempt", status(
                "failure", reason=check.STATUS_REASONS["unread_inputs"])),
            GallerySample("unread-manual", "incomplete", "Required inputs unread, manual attempt with workflow changes", status(
                "failure", head=fixtures.OTHER, owner=manual_owner, trigger_label="Manual request",
                reason=check.STATUS_REASONS["unread_inputs"])),
            GallerySample("workflow-changed", "incomplete", "Relevant review workflow changed, likely cause unconfirmed", status(
                "failure", reason=check.STATUS_REASONS["workflow_changed"])),
            GallerySample("workflow-changed-superseded", "incomplete", "Workflow-change context retained for a superseded attempt", status(
                "stale", head=fixtures.OTHER, owner_running=False,
                reason=check.stale_status_reason(check.STATUS_REASONS["workflow_changed"]))),
        ]
        for group, state, result in (("running-history", "in_progress", "success"),
                                     ("incomplete-history", "failure", "action_required"),
                                     ("publication-incomplete", "publication_incomplete", "success")):
            samples.append(GallerySample(f"{group}-manual", group, f"Manual current attempt with previous completed review: {state}", status(
                state, head=fixtures.OTHER, owner=manual_owner, trigger_label="Manual request",
                last_review=history(result), last_review_details=details,
                **({"reason": check.STATUS_REASONS["check_publication_failed"] if state == "publication_incomplete"
                    else check.STATUS_REASONS["incomplete"]} if state != "in_progress" else {}))))
        shared_owner = fixtures.presentation_owner(
            head=fixtures.OTHER, base=fixtures.BASE_TIP, run=5,
            started=STARTED + 600_000_000_000,
        )
        for state in ("in_progress", "failure"):
            samples.append(GallerySample(f"shared-run-{state}".replace("_", "-"), "shared-run", f"Shared workflow run, matching historical baseline: {state}", status(
                state, head=fixtures.OTHER, owner=shared_owner,
                last_review=history("success"), last_review_details=details,
                **({"reason": check.STATUS_REASONS["incomplete"]} if state == "failure" else {}))))
        os.environ["DETAILS_URL"] = "https://github.com/owner/repo/actions/runs/6"
        samples.append(GallerySample("legacy-history-run", "legacy-history", "Legacy completed history without recorded attempt metadata", status(
            "stale", owner=None, last_review=history("success"),
            last_review_details={"run_url": details["run_url"]}, owner_running=False)))
        os.environ["DETAILS_URL"] = ""
        samples.append(GallerySample("legacy-history-no-links", "legacy-history", "Legacy completed review without workflow links", status(
            "stale", owner=None, head=fixtures.OTHER, last_review=history("success"),
            last_review_details={"base_sha": fixtures.BASE_TIP}, owner_running=False,
            completed_owner_verified=True, reason=check.STATUS_REASONS["stale"])))
        os.environ["DETAILS_URL"] = details["run_url"]
        for moved in ("head", "base", "ref"):
            samples.append(GallerySample(f"stale-running-{moved}", "stale-running", f"Stale running attempt without completed history: changed {moved}", status(
                "stale", head=fixtures.OTHER if moved == "head" else fixtures.HEAD,
                base_ref="release" if moved == "ref" else "main",
                base_sha=fixtures.OTHER if moved == "base" else fixtures.BASE_TIP,
                owner_running=True, attempt_trigger="Draft marked ready",
                reason=check.STATUS_REASONS["stale"])))
        samples.append(GallerySample("stale-manual", "stale-running", "Stale manual attempt with distinct completed-review trigger", status(
            "stale", head=fixtures.OTHER, owner=manual_owner, owner_running=True,
            attempt_trigger="Manual request", last_review=history("success"),
            last_review_details=details, reason=check.STATUS_REASONS["stale"])))
        grouped = {group: [] for group in GROUP_ORDER}
        for case in samples:
            grouped[case.group].append(case)
        ordered = [case for group in GROUP_ORDER for case in grouped[group]]
        first = ("current-clean", "plain-selection", "effort-only", "opus-observed",
                 "opus-mismatch", "opus-unknown", "opus-multiple", "current-findings",
                 "selection-findings", "selection-running", "selection-incomplete",
                 "selection-tooling-unavailable")
        by_id = {case.case_id: case for case in ordered}
        return [*[by_id[key] for key in first], *[case for case in ordered if case.case_id not in first]]


def sample_markdown(body):
    """Preserve rendered hard breaks without trailing whitespace in Git diffs."""
    return body.replace("  \n", "\\\n")


def render_gallery():
    sections = ["\n\n".join([
        f"# {HEADING}",
        "All sample revisions, runs, timestamps, and consumer repositories are synthetic. "
        "Runtime repository names identify this project; their sample revisions are synthetic. "
        "Sample links may intentionally have no live destination. The raw Markdown "
        "retains complete synthetic production metadata for auditing.",
        f"Generated with `{REGENERATE}`. Edit the generator or sample inputs, then regenerate.",
    ])]
    for number, case in enumerate(gallery_samples(), start=1):
        sample_body = sample_markdown(case.body)
        sections.append(f'<a id="{case.case_id}"></a>\n\n### {number} — {case.title.replace(": in_progress", ": running").replace(": failure", ": incomplete").replace(": publication_incomplete", ": publication unavailable")}\n\n{sample_body}')
        if case.case_id == FINDINGS_SAMPLE:
            # Findings live in inline threads, outside the status renderer.
            sections[-1] += ("\n\n**Synthetic inline finding context (separate from the status comment):**\n\n"
                             + SYNTHETIC_FINDING)
    return "\n\n---\n\n".join(sections) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="regenerate the committed gallery")
    mode.add_argument("--check", action="store_true", help="fail if the committed gallery is missing or stale")
    args = parser.parse_args(argv)
    output = render_gallery().encode("utf-8")
    if args.check:
        if not GALLERY.is_file() or GALLERY.read_bytes() != output:
            print(f"Gallery is missing or stale. Regenerate with: {REGENERATE}", file=sys.stderr)
            return 1
    elif args.write:
        GALLERY.write_bytes(output)
    else:
        sys.stdout.write(output.decode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
