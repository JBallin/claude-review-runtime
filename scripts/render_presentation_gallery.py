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


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "presentation_gallery_fixtures", ROOT / "tests" / "test_claude_review_check.py"
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
check = fixtures.check

HEADING = "SAMPLE ONLY — visual QA gallery, not actual review results"
GALLERY = ROOT / "docs" / "review-presentation-gallery.md"
REGENERATE = "python3 -B scripts/render_presentation_gallery.py --write"
FINDINGS_SAMPLE = "A2 — Current findings completion, matching baseline"
SYNTHETIC_FINDING = (
    "`src/cache.py:42` — **[P2] Invalidate cached entries after deletion.** "
    "Deleting an item removes it from storage but leaves its cached value intact, "
    "so subsequent reads can return the deleted item until the cache expires. "
    "Invalidate the cache entry after a successful deletion."
)
COMPLETED_AT = "2026-10-07T13:09:19Z"
STARTED = 1791378000000000000  # 2026-10-07T13:00:00Z
OPERATIONAL_MARKER = re.compile(r"<!-- claude-review-runtime:[^\r\n]*? -->\n?")


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
    """Return labeled raw helper output for representative supported states."""
    with fixture_environment(), mock.patch.object(check, "now", return_value=COMPLETED_AT):
        owner = fixtures.presentation_owner(started=STARTED)
        details = {
            "base_sha": fixtures.BASE_TIP,
            "run_url": "https://github.com/owner/repo/actions/runs/5",
            "completed_at": COMPLETED_AT,
            "trigger": "PR opened for review",
            "generation": owner["generation"],
        }

        def status(state, *, head=fixtures.HEAD, base_ref="main", **options):
            return check.status_comment_body(
                head, base_ref, state, **{
                    "owner": owner,
                    "trigger_label": "PR opened for review",
                    "completed_at": COMPLETED_AT,
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
        samples = [
            ("A — Clean completion, matching baseline", status("success")),
            (FINDINGS_SAMPLE, status("action_required")),
            ("B — Clean completion, changed target branch", status(
                "stale", last_review=history("success"), base_sha=fixtures.OTHER,
                **stale_options)),
            ("C — Findings, changed target branch", status(
                "stale", last_review=history("action_required"), base_sha=fixtures.OTHER,
                **stale_options)),
            ("D — PR commit changed after clean completion", status(
                "stale", head=fixtures.OTHER, last_review=history("success"),
                base_sha=fixtures.BASE_TIP, **stale_options)),
            ("E — Incomplete attempt without completed history", status(
                "failure", reason=check.STATUS_REASONS["incomplete"])),
            ("F — Compact clean completion, matching baseline", check.completion_comment_body(
                fixtures.REPO, fixtures.HEAD, "main", fixtures.BASE_TIP,
                check.completion_marker(fixtures.REPO, 7, fixtures.HEAD, "main",
                                        fixtures.BASE_TIP, fixtures.MERGE_BASE),
                "https://github.com/owner/repo/actions/runs/5",
                observed_base_sha=fixtures.BASE_TIP)),
            ("G — Compact clean completion, differing baseline", check.completion_comment_body(
                fixtures.REPO, fixtures.HEAD, "main", fixtures.BASE_TIP,
                check.completion_marker(fixtures.REPO, 7, fixtures.HEAD, "main",
                                        fixtures.BASE_TIP, fixtures.MERGE_BASE),
                "https://github.com/owner/repo/actions/runs/5",
                observed_base_sha=fixtures.OTHER)),
        ]
        newer_owner = fixtures.presentation_owner(
            head=fixtures.OTHER, base=fixtures.NEWER, run=6,
            started=STARTED + 600_000_000_000,
        )
        for state, sample, description in (
                ("in_progress", "H", "Running attempt with older completed history"),
                ("failure", "I", "Incomplete attempt with older completed history")):
            for result, label in (("success", "clean"), ("action_required", "findings")):
                options = {"reason": check.STATUS_REASONS["incomplete"]} if state == "failure" else {}
                samples.append((f"{sample} — {description}: {label}", status(
                    state, head=fixtures.OTHER, owner=newer_owner, trigger_label="Draft marked ready",
                    last_review=history(result), last_review_details=details, **options)))
        for result, label in (("success", "clean"), ("action_required", "findings")):
            samples.append((f"J — Retargeted provenance: {label}", status(
                "stale", base_ref="release", last_review=history(result),
                base_sha=fixtures.BASE_TIP, **stale_options)))
        for sample, state, available, description in (
                ("K", "publication_incomplete", True, "Authoritative Check publication incomplete"),
                ("L", "publication_incomplete", False, "Completion with unavailable Check publication"),
                ("M", "in_progress", False, "Running with unavailable Check publication")):
            for result, label in ((None, "no completed history"),
                                  ("success", "previous clean review"),
                                  ("action_required", "previous findings")):
                options = ({"reason": check.STATUS_REASONS["check_publication_failed"]}
                           if state == "publication_incomplete" else {})
                samples.append((f"{sample} — {description}: {label}", status(
                    state, head=fixtures.OTHER, owner=newer_owner,
                    trigger_label="Draft marked ready", check_available=available,
                    last_review=history(result) if result else None,
                    last_review_details=details if result else None, **options)))
        manual_owner = fixtures.presentation_owner(
            kind="issue_comment", target=11, head=fixtures.OTHER, base=fixtures.NEWER,
            run=6, started=STARTED + 600_000_000_000,
        )
        for letter, state, result in (("H", "in_progress", "success"),
                                      ("I", "failure", "action_required"),
                                      ("K", "publication_incomplete", "success")):
            samples.append((f"{letter} — Manual current attempt with previous completed review: {state}", status(
                state, head=fixtures.OTHER, owner=manual_owner, trigger_label="Manual request",
                last_review=history(result), last_review_details=details,
                **({"reason": check.STATUS_REASONS["check_publication_failed"] if state == "publication_incomplete"
                    else check.STATUS_REASONS["incomplete"]} if state != "in_progress" else {}))))
        shared_owner = fixtures.presentation_owner(
            head=fixtures.OTHER, base=fixtures.BASE_TIP, run=5,
            started=STARTED + 600_000_000_000,
        )
        for letter, state in (("N", "in_progress"), ("O", "failure")):
            samples.append((f"{letter} — Shared workflow run, matching historical baseline: {state}", status(
                state, head=fixtures.OTHER, owner=shared_owner,
                last_review=history("success"), last_review_details=details,
                **({"reason": check.STATUS_REASONS["incomplete"]} if state == "failure" else {}))))
        os.environ["DETAILS_URL"] = "https://github.com/owner/repo/actions/runs/6"
        samples.append(("P — Legacy completed history without recorded attempt metadata", status(
            "stale", owner=None, last_review=history("success"),
            last_review_details={"run_url": details["run_url"]}, owner_running=False)))
        os.environ["DETAILS_URL"] = ""
        samples.append(("Q — Legacy completed review without workflow links", status(
            "stale", owner=None, head=fixtures.OTHER, last_review=history("success"),
            last_review_details={"base_sha": fixtures.BASE_TIP}, owner_running=False,
            completed_owner_verified=True, reason=check.STATUS_REASONS["stale"])))
        os.environ["DETAILS_URL"] = details["run_url"]
        for moved in ("head", "base", "ref"):
            samples.append((f"R — Stale running attempt without completed history: changed {moved}", status(
                "stale", head=fixtures.OTHER if moved == "head" else fixtures.HEAD,
                base_ref="release" if moved == "ref" else "main",
                base_sha=fixtures.OTHER if moved == "base" else fixtures.BASE_TIP,
                owner_running=True, attempt_trigger="Draft marked ready",
                reason=check.STATUS_REASONS["stale"])))
        samples.append(("R — Stale manual attempt with distinct completed-review trigger", status(
            "stale", head=fixtures.OTHER, owner=manual_owner, owner_running=True,
            attempt_trigger="Manual request", last_review=history("success"),
            last_review_details=details, reason=check.STATUS_REASONS["stale"])))
        return samples


def render_gallery():
    sections = ["\n\n".join([
        f"# {HEADING}",
        "All sample repository, commit, run, and timestamp identities are synthetic. "
        "Sample links may intentionally have no live destination. Operational Runtime "
        "markers are omitted; these samples are not review records.",
        f"Generated with `{REGENERATE}`. Edit the generator or sample inputs, then regenerate.",
    ])]
    for title, body in gallery_samples():
        sample_body = strip_operational_markers(body)
        sections.append(f"### {title}\n\n{sample_body}")
        if title == FINDINGS_SAMPLE:
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
