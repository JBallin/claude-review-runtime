# Consumer workflows

*Use this guide to install and operate the three reusable review entrypoints in a consumer repository.*

## Prerequisites

This runtime repository is public. Consumers can reference its reusable
workflows by commit SHA, subject to their GitHub Actions policies. Publication
does not validate a new consumer configuration; the tested boundaries are listed
below and in the [validation summary](validation.md).

Model authentication uses the consumer's existing Claude OAuth credential stored
as the Actions secret `CLAUDE_CODE_OAUTH_TOKEN`. This runtime does not accept
`ANTHROPIC_API_KEY`; API-key support is deferred (see the
[authentication assessment](https://github.com/JBallin/claude-review-runtime/issues/3#issuecomment-5941743869)).
This is the runtime's contract, not a limitation of Anthropic's action.
GitHub authentication uses the Anthropic Claude GitHub App and the action's
default OIDC token exchange. The App must have access to the consumer. No PAT,
custom App credential, or GitHub token override is accepted by the runtime. Adding credentials or access grants
requires the repository owner's authorization.

The supported platform is GitHub.com with GitHub-hosted Ubuntu runners. The
runtime-local helper action uses `$/actions/review-helper`, which requires runner
version 2.336.0 or later. Broader public-consumer coverage, other-owner, fork, and
GitHub Enterprise Server configurations remain unvalidated. See the
[validation summary](validation.md) for tested revisions. The runtime is licensed
under the [MIT License](../LICENSE).

## Install the callers

Install three separate workflow files in the consumer repository. Replace
`<full-reviewed-runtime-sha>` with one reviewed 40-character commit SHA in all
three files. Have the caller installation reviewed by a human and merged to
the consumer's default branch, then validate on a separate PR that does not
modify the workflows. See [installation and incomplete reviews](review-guide.md#installation-and-incomplete-reviews)
for default Anthropic App/OIDC validation limits and completion evidence.

These examples forward event context directly to the reusable workflows. Add
any consumer-specific branch or eligibility restrictions to the calling jobs;
do not add prompt, tool, helper-path, or credential overrides.

## Automatic review

```yaml
name: Automatic Claude review
on:
  pull_request:
    types: [opened, ready_for_review]

permissions: {}

jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
      checks: write
      issues: write
      id-token: write
    uses: JBallin/claude-review-runtime/.github/workflows/claude-review.yml@<full-reviewed-runtime-sha>
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

The runtime preserves draft, fork, and Dependabot exclusions. Automatic review
captures the pull-request event's head and base; it does not silently move the
review to a later patch.

For the selected revision's publication behavior, see the
[finding-publication contract](review-guide.md#finding-publication).

## Manual review

Manual invocation depends on the selected runtime revision. See the review
guide for [this repository's pinned command](review-guide.md#request-a-manual-review)
before selecting a revision or changing callers.

```yaml
name: Manual Claude review
on:
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]

permissions: {}

jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
      checks: write
      issues: write
      id-token: write
    uses: JBallin/claude-review-runtime/.github/workflows/claude.yml@<full-reviewed-runtime-sha>
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

For the current reusable entrypoint, post `/claude-review` as the entire comment,
without whitespace or arguments; matching is case-insensitive. This repository’s
self-review callers use that same command with their reviewed runtime pin. Any additional manual
caller gate must match the selected runtime revision.

The runtime resolves the current patch once for each accepted manual request.
Ordinary issue comments and untrusted requests do not start model execution.

## Stale presentation refresh

```yaml
name: Claude review status
on:
  pull_request:
    types: [synchronize, edited]

permissions: {}

jobs:
  status:
    permissions:
      contents: read
      pull-requests: write
      issues: write
    uses: JBallin/claude-review-runtime/.github/workflows/claude-review-status.yml@<full-reviewed-runtime-sha>
```

This entrypoint needs no OAuth secret, OIDC permission, or Check write permission.
It refreshes existing presentation after head changes and qualifying base
retargeting edits; it does not create a review when none exists.

## Permission and failure boundaries

The calling review jobs grant the permission envelope required by the reusable
workflow. The called jobs narrow that envelope independently: the model job
receives reads and OIDC, while trusted publication jobs receive writes. The
runtime serializes automatic, manual, and status work in one per-consumer-PR
queue with cancellation disabled. A caller-level lock using the same group can
interfere with that queue.

Workflow access, runtime-action resolution, and runner preparation happen
before helper steps can execute. If those stages fail, the helper's fallback
cannot publish a failure Check. Inspect the workflow run as well as the exact-head
Check, and verify actual model execution and completion evidence during the first
live trial.

For review evidence, trust boundaries, update and rollback guidance, and this
repository's additional caller gates, see the [review guide](review-guide.md).
