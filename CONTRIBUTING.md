# Contributing

This is an experimental runtime with no commitment to ongoing support.
Start with the [consumer guide](docs/consumer-workflows.md),
[review contract](docs/review-guide.md), and
[validation boundaries](docs/validation.md).

## Local checks

Use Python 3 and run the standard-library test suite from the repository root:

```sh
python3 -m unittest discover -s tests -v
git diff --check
```

Include the commands and results in your pull request. Use deterministic,
sanitized reproductions for bugs. Never include credentials, private project
content, or exploit details in public issues or pull requests.
See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

### Review presentation gallery

Generate the offline, synthetic gallery with the [sample command](scripts/render_presentation_gallery.py):

```sh
python3 -B scripts/render_presentation_gallery.py --source-revision "$(git rev-parse HEAD)"
```

It writes Markdown to stdout using the actual presentation helpers and test
fixtures. Operational review markers are omitted; unsupported scenarios are
labeled pending. Hidden legacy Reason receipts remain for rendering checks.
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
the maintainer can perform the checklist instead.

## Change scope

Keep changes focused and explain the trigger, expected result, and observed
behavior. Preserve captured patch identity, verified completion, sticky findings,
and trusted publication ownership. Regression tests should exercise the affected
contract rather than infer clean completion from missing evidence.

Runtime changes and consumer adoption are separate reviewed changes. After a
validated group of runtime changes, consider a separate PR to update this
repository’s self-review pins. Keep all
three consumer pins coherent, and preserve historical Checks, findings, notices,
and unrelated reactions during recovery or rollback. Review the permission and
authentication boundaries before changing workflow inputs or dependencies.

Local tests use simulated faults and make no model calls. Live exercises need a
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
