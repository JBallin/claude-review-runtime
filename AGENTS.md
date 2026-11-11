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

Follow the [review presentation QA process](docs/review-presentation-qa.md) for
changes to generated review output. It defines gallery generation, scenario
coverage, GitHub-rendered desktop and narrow checks, keyboard and semantic
checks, screenshots, and the evidence required in the PR description.

Complete applicable local validation and independent review before authorized
publication. Complete the required GitHub-rendered checks against the published
candidate before marking ready or declaring merge readiness. Keep blocked or
unrun checks explicit; local previews do not substitute for GitHub rendering.
Spoken screen-reader, physical-device, and live-comment checks are separate
coverage, not routine gallery requirements or implied by a routine pass.

The process preserves existing browser approval boundaries. Do not change
permissions, publish, or switch destinations to work around denied access.

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
