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

## Finding publication

Both review entrypoints publish inline findings immediately. Trusted completion
requires a valid receipt for every inline attempt and confirms each comment on
the captured PR/head using the complete paginated comment list and pre-review
snapshot. Failed or ambiguous publication produces an incomplete review, retains
any findings already recorded, and cannot produce a clean completion notice.

The complete receipt list is limited to 64 finding attempts per run. Exceeding
that limit fails completion; receipt evidence is never truncated to imply clean
publication. The limit is separate from the sampled historical Check diagnostics.
An explicit zero-attempt receipt list permits clean completion only when all
other completion and finding-evidence checks pass. No additional consumer input,
credential, or permission is required.

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

Post `/claude-review` as the entire PR comment, without surrounding whitespace,
arguments, quotes, or other text. Matching is case-insensitive. Top-level PR
comments and inline review comments are accepted from authors GitHub identifies
as `OWNER`, `MEMBER`, or `COLLABORATOR`; the action also checks actor/access
eligibility. Ordinary issue comments and untrusted requests do not start model
execution. The runtime resolves the current patch once for each accepted request.

The command deliberately avoids a Claude mention. `@claude review` can invoke
Anthropic's separate managed review service and does not invoke this runtime.
Provider-bot reactions on comments are preserved and are not this runtime's
progress or completion signals. Runtime revisions that accepted mentions retain
that behavior while pinned; lab callers testing this command need the updated
runtime pin. The caller workflow shape stays the same.

The exact-SHA `Claude Review` Check is authoritative. The stable workflow-owned
status comment and top-level PR reactions present progress, clean completion,
findings, failure, and stale state. A verified clean manual review also adds a
fixed completion notice after successful Check publication, identifying its
captured head, base commit, and workflow run. Reserve this runtime's namespaced
comment markers for its trusted publication jobs.

Completion notices are historical information, not review authority or merge
approval. There is at most one notice per PR/head/base-commit/base-ref/merge-base
identity. Repeating the command still reviews the patch and refreshes Check/status
state, but a clean rerun of the same identity adds no notice. Findings or a failed
rerun leave prior notices unchanged; consult the current Check and status. A base
change creates a different notice identity even when the head is unchanged.

No success notice is posted for findings, failure, unverified completion, or an
unavailable Check. Publication checks the live head and base immediately before
posting and skips known superseded snapshots. A concurrent PR change can still
occur afterward, so the notice always describes its linked historical snapshot.
Notice publication is best-effort: an API failure may leave no notice, and an
ambiguous POST is not retried blindly. Neither changes the authoritative result.

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
