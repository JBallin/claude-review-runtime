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

## Change scope

Keep changes focused and explain the trigger, expected result, and observed
behavior. Preserve captured patch identity, verified completion, sticky findings,
and trusted publication ownership. Regression tests should exercise the affected
contract rather than infer clean completion from missing evidence.

Runtime changes and consumer adoption are separate reviewed changes. Keep all
three consumer pins coherent, and preserve historical Checks, findings, notices,
and unrelated reactions during recovery or rollback. Review the permission and
authentication boundaries before changing workflow inputs or dependencies.

Local tests use simulated faults and make no model calls. Live exercises need a
separately approved fixture matrix, model-run and time budget, concurrency and
retry bounds, stop conditions, and restoration plan. Do not broaden credentials
or access to work around a rejected run.

## API-key variant maintenance

`main` remains OAuth-only. The experimental `api-key` branch accepts only
`ANTHROPIC_API_KEY` and is not live-API-tested. Keep the variant delta limited to
the model secret/input, the fixed missing-key diagnostic, contract tests, and
branch-specific guidance. Preserve the pinned action, GitHub App/OIDC,
permissions, immediate publication and receipt checks, and trusted finalizer.
The installed self-review callers remain pinned to their reviewed OAuth runtime;
do not replace their secret names without separately reviewing adoption.

After changes merge to `main`, assess their effect on the variant and use an
isolated checkout to fetch and merge `origin/main` into `api-key`. Inspect
incoming changes and every conflict,
preserving both main's fixes and the API-key-only contract. Never rebase,
force-push, delete/recreate the published branch, or otherwise rewrite its
published history. Publish only fast-forward updates to `api-key`, so every
previously published API-key commit remains reachable for pinned consumers.
Do not merge this variant into `main`. No scheduled sync automation is required.

### Post-merge checklist

- Record the main SHA merged and the API-key candidate SHA. Review the effective
  delta against main for auth, documentation, and dependency drift.
- Preserve main fixes through a normal merge rather than copying or duplicating
  their work. Review interactions with the variant before publishing the merge.
- Run `python3 -m unittest discover -s tests -v` and `git diff --check`.
  Confirm both automatic and manual auth contracts, immediate publication and
  receipt verification, trusted finalization, permissions, and captured identity.
- Confirm README links, consumer secret examples and experimental/not-live-tested
  labels. Record offline evidence separately from any authorized live evidence.
- Before publishing, verify the prior remote API-key head is an ancestor of the
  candidate (`git merge-base --is-ancestor <prior-api-key-sha> HEAD`). If the
  remote advanced, merge it and review/retest the combined candidate. Use a
  normal push; stop on rejection rather than force-pushing.
- Keep all three consumer pins coherent during any separately reviewed adoption.
  Old pins remain valid history references; branch sync does not update consumers.

Live exercises require separate credential and spending authorization under the
local-check guidance above. Until then, do not claim validated API-key support.
