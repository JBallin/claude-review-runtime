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


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "presentation_gallery_fixtures", ROOT / "tests" / "test_claude_review_check.py"
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
check = fixtures.check

HEADING = "SAMPLE ONLY — visual QA gallery, not actual review results"
SOURCE_REPOSITORY = "https://github.com/JBallin/claude-review-runtime"
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
    """Return labeled raw helper output for every supported gallery state."""
    with fixture_environment():
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
        return samples


def source_revision(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("source revision must be a full lowercase 40-character commit SHA")
    return value


def render_gallery(revision):
    revision = source_revision(revision)
    sections = ["\n\n".join([
        f"# {HEADING}",
        "All sample repository, commit, run, and timestamp identities are synthetic. "
        "Sample links may intentionally have no live destination. Operational Runtime "
        "markers are omitted; these samples are not review records.",
        f"**Source revision:** [{revision}]({SOURCE_REPOSITORY}/commit/{revision})",
    ])]
    for title, body in gallery_samples():
        sample_body = strip_operational_markers(body)
        sections.append(f"### {title}\n\n{sample_body}")
    sections.append("\n\n".join([
        "## Visual-QA observations",
        "**Not yet inspected.** Inspect this revision at desktop (about 1280 px) and "
        "narrow (about 390 px) widths, with every Details section collapsed and expanded. "
        "Record the actual widths, revision, and observed spacing, tables, warnings, "
        "identities, links, and hidden-receipt suppression here. Regeneration resets this status.",
    ]))
    return "\n\n---\n\n".join(sections) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True, type=source_revision)
    args = parser.parse_args(argv)
    sys.stdout.write(render_gallery(args.source_revision))


if __name__ == "__main__":
    main()
