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

After changes merge to `main`, update the experimental `api-key` branch in an
isolated checkout with a normal merge of `origin/main`. Inspect the minimal
authentication and documentation delta, preserving OAuth-only `main` and the
API-key-only variant. Never rebase or force-push published history; keep old
API-key commits reachable for pinned consumers. Run
`python3 -m unittest discover -s tests -v` and `git diff --check` before a normal
push. Keep the variant labeled experimental and not live-API-tested.
