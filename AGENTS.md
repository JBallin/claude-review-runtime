# Repository instructions

## Development and validation

Keep changes focused on the requested outcome. Preserve captured patch identity,
verified completion, sticky findings, and trusted publication ownership. For
presentation changes, keep operational markers and parser compatibility intact;
review eligibility, reactions, and Check authority must retain their contracts.

Use Python 3 and run the standard-library regression suite from the repository root:

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

Generate the offline, synthetic gallery with the [sample command](scripts/render_presentation_gallery.py):

```sh
python3 -B scripts/render_presentation_gallery.py --source-revision "$(git rev-parse HEAD)"
```

It writes Markdown to stdout using the actual presentation helpers and test
fixtures. Operational review markers are omitted; all supported states are
rendered from those helpers. Hidden legacy Reason receipts remain for rendering
checks.
Generation makes no network calls or persistent changes.

After publication is authorized, verify the clean generating checkout matches
the published PR head and the gallery's full source revision. Publish it as one
top-level PR comment through the GitHub API/CLI, retain its ID and direct link,
and update that same comment after presentation changes. If its ID is lost,
find the unique matching gallery heading and publisher across paginated comments;
reconcile ambiguity or uncertain writes before creating another comment. Keep
the payload synthetic and free of reviewer invocation text; publication must
not invoke Claude or change review state.

Inspect the rendered PR comment at desktop (about 1280 px) and narrow (about
390 px) widths with every Details section collapsed and expanded. Check spacing,
tables, warnings, identity labels, link targets, and hidden-receipt suppression.
Record observed results, actual widths, and inspected revision in the gallery's
QA section; reset results after regeneration. Markdown generation alone does
not complete visual QA. Agent browser inspection needs scoped UI authorization;
report any blocked browser access or sizing check precisely instead of
claiming visual QA passed.

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

After changes merge to `main`, merge `main` into `api-key`. Preserve API-key-only
authentication and run the local checks. Routine merges of already-approved
`main` changes can use a normal push without another PR. Substantive changes,
including API-specific behavior or nontrivial conflict resolutions, require a PR.
Never rebase or force-push published history; keep old commits reachable for
pinned consumers.
