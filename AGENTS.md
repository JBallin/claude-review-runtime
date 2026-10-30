# Repository instructions

## Development and validation

Keep changes focused on the requested outcome. Preserve captured patch identity,
verified completion, sticky findings, and trusted publication ownership. For
presentation changes, keep operational markers and parser compatibility intact;
review eligibility, reactions, and Check authority must retain their contracts.

Use Python 3. For code, configuration, workflow, or test changes, run targeted
local checks and regression tests for the affected contracts before publication
or review. For documentation-only changes, check the affected documentation,
links, and whitespace without automatically repeating the full local suite.

CI runs the full standard-library regression suite and whitespace checks in
[the validation workflow](.github/workflows/ci.yml); it remains the full-suite
gate before merge. Run the full suite locally when cross-cutting risk warrants
it, needed evidence is unavailable from CI, diagnosis requires it, or an explicit
instruction requires it. Run these commands from the repository root when
performing full local validation:

```sh
python3 -B -m unittest discover -s tests -v
git diff --check
```

Use focused tests for the changed contracts, including captured-patch manual
completion when touching review presentation. Reuse deterministic sanitized
fixtures and stub integrations; local tests must make no model calls. Retain
long-running command handles, logs, and final status before considering reruns.
Report the exact candidate and any skipped, failed, or pending coverage.

Before publishing, reconcile live PR feedback and maintain its actionable
checklist in the task or PR record. Address in-scope findings, run affected
validation and local final review, then use follow-up commits and scoped pushes.
Refresh the published head/base and finalize that exact revision. Report changes
since the previous review and unresolved items; do not initiate reviews or mark
ready without the applicable authorization.

### Review presentation gallery

Regenerate and check the committed [synthetic gallery](docs/review-presentation-gallery.md)
with the existing [generator](scripts/render_presentation_gallery.py):

```sh
python3 -B scripts/render_presentation_gallery.py --write
python3 -B scripts/render_presentation_gallery.py --check
```

The generator uses actual presentation helpers and fixed synthetic fixtures,
without network, browser, or model calls. CI checks exact bytes and fails on
missing or stale output; it never regenerates the committed file. Keep behavioral
regression tests alongside freshness checks. Complete synthetic production metadata is retained in the raw gallery; rendered
HTML comments remain hidden. Markdown hard breaks
use equivalent backslashes to avoid trailing whitespace in Git diffs. Edit the
renderer or sample inputs, then regenerate; keep observations out of the generated
file.

Review the gallery diff and inspect affected rendered states. When metadata,
operational markers, or their generation paths change, verify the intended
semantics against source and focused tests, including evidence not visible in
the gallery. The raw gallery retains operational markers for audit; verify their contracts
against the production helpers and focused tests. Focus on the changes; a full audit of unchanged metadata is not
required for every PR.

When a change adds or materially alters a rendered state, add or update
representative scenarios in the gallery generator and visually inspect the
resulting output. Cover genuinely distinct behavior, including relevant
conditional, error and fallback states, rather than every possible combination
or a new example for every issue.

When emergency workflow presentation changes, also capture synthetic output from
both workflows through `ToolingFallbackScriptTests.run_fallback` (stubbed GitHub
calls and temporary fixtures). Keep matching outputs as labeled QA adjuncts
in the PR review evidence, removing operational markers. This harness is separate
from the gallery generator; do not duplicate fallback templates or append
hand-edited output to the committed gallery.

Before authorized draft publication or a PR update, complete applicable local
tests, generated-gallery freshness and scope checks, and independent review.
Label local Markdown previews separately; they do not establish GitHub-rendered
QA.

After authorized publication, inspect the exact published gallery in GitHub's
renderer during PR finalization, before marking ready or declaring merge
readiness. Verify the generating checkout and committed gallery match the exact
published PR head. In an open PR description, use one branch-based gallery
preview link. Record the exact inspected commit as plain text
alongside inspected scenarios, visual-QA results, and meaningful limitations;
a second commit-pinned gallery link is not required. Do not embed the gallery's
own commit SHA in the generated file. Do not paste the full gallery or duplicate
the Files changed diff into PR comments or the description. Keep all samples
synthetic and free of reviewer invocation text.

For that GitHub-rendered check, inspect the gallery at desktop (about 1280 px)
and narrow (about 390 px) widths. Check Details collapsed and expanded for changed
or affected states plus representative unchanged cases. Check every section when
a shared renderer or layout change could affect the whole gallery. Check spacing,
tables, warnings, identity labels, link targets, and hidden-receipt suppression.
Record the inspected scenarios, coverage rationale, observed results, actual
widths, and inspected revision in the PR description. Markdown generation alone
does not complete visual QA.

Routine read-only QA of this repository's local or public synthetic gallery is
included in an already authorized engineering task: desktop/narrow sizing,
opening and closing Details, and screenshots do not need repeated separate
approval. Continue to follow platform and assistant approval controls. This
scope does not authorize other sites or browser interactions, sign-in or account
settings, external publication, or access/permission changes. Report any blocked
browser access or sizing check precisely instead of claiming visual QA passed.
If GitHub-rendered coverage is unavailable or denied, report what remains
unverified and hold transitions that require it. Do not publish or switch
destinations to work around a browser denial.

Include representative desktop and narrow screenshots of changed rendered views
in the task handoff, identifying the source revision and scenarios shown.
Screenshots supplement inspection; an image is not required for every example.
If capture is blocked, report the precise limitation instead of claiming
screenshots were supplied.

## Scope and runtime adoption

Regression tests should exercise the affected contract rather than infer clean
completion from missing evidence.

Runtime changes and consumer adoption are separate reviewed changes. After a
validated group of runtime changes, consider a separate PR to update this
repository’s self-review pins. Keep all
three consumer pins coherent, and preserve historical Checks, findings, notices,
and unrelated reactions during recovery or rollback. Review the permission and
authentication boundaries before changing workflow inputs or dependencies.

Live exercises need a
separately approved fixture matrix, model-run and time budget, concurrency and
retry bounds, stop conditions, and restoration plan. Do not broaden credentials
or access to work around a rejected run.

## API-key variant maintenance

This procedure applies to periodic or explicitly requested variant maintenance,
not to every PR.

After changes merge to `main`, merge `main` into `api-key`. Preserve API-key-only
authentication and run the local checks. Routine merges of already-approved
`main` changes can use a normal push without another PR. Substantive changes,
including API-specific behavior or nontrivial conflict resolutions, require a PR.
Never rebase or force-push published history; keep old commits reachable for
pinned consumers.
