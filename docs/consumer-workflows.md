# Consumer workflows

Install three separate workflow files in the consumer repository. Replace
`<full-reviewed-runtime-sha>` with one reviewed 40-character commit SHA in all
three files. Keep the files on the consumer's default branch before testing the
default Anthropic App/OIDC path.

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

## Manual review

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

The runtime requires a trusted author and a Claude mention on a pull request. It
resolves the current patch once for the manual request. Ordinary issue comments
and untrusted requests do not start model execution.

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

Private workflow access, runtime-action resolution, and runner preparation happen
before helper steps can execute. If those stages fail, the helper's fallback
cannot publish a failure Check. Inspect the workflow run as well as the exact-head
Check, and verify actual model execution and completion evidence during the first
live trial.
