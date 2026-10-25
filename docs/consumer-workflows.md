# Consumer workflows

*Use this guide to install and operate the three reusable review entrypoints in a consumer repository.*

## First-review checklist

1. Choose `main` (OAuth) or `api-key` and meet the
   [prerequisites](#prerequisites), including Claude GitHub App access. Obtain the
   selected model credential using [Anthropic's authentication instructions](https://code.claude.com/docs/en/authentication)
   (`claude setup-token` for OAuth, or a Claude Console API key), then
   [save it as a repository Actions secret](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets#creating-secrets-for-a-repository)
   under the branch's exact secret name below. Never commit the credential.
2. Choose one reviewed, full immutable 40-character SHA from that
   authentication-compatible branch for all three callers; consult the
   [validation summary](validation.md) for validation choices and coverage limits.
   The `api-key` branch is not live-API-tested; publication alone does not
   validate a pin.
3. [Install the callers](#install-the-callers) as three separate files under
   `.github/workflows/`: [automatic](#automatic-review), [manual](#manual-review),
   and [stale status](#stale-presentation-refresh). Forward only the selected
   model secret to the two review callers; the status caller needs none. Have a
   human review and merge the installation to the consumer's default branch.
4. For an approved first live trial, open a non-draft, same-repository PR that
   does not modify workflows (or mark its draft ready) to trigger automatic
   review. For an existing eligible PR, use the selected revision's
   [manual command](#manual-review). Follow the
   [installation guidance](review-guide.md#installation-and-incomplete-reviews)
   if caller validation or another prerequisite prevents completion.
5. Inspect the workflow run and the `Claude Review` Check on that exact head,
   including captured head/base, runtime revision, and verified model completion.
   Clean means verified completion without recorded findings; findings need
   assessment, and incomplete is not approval. See the
   [review-result guidance](review-guide.md#trust-and-review-results); green Actions
   orchestration or a thumbs-up alone does not prove completion.
6. After changing the patch, request a fresh review with the
   [manual command](#manual-review). The [status caller](#stale-presentation-refresh)
   refreshes existing presentation; it does not run Claude or review the new
   patch. Assess changed-base coverage using the same review-result guidance.

## Required reviews and status checks

Keep completed Claude review and explicit disposition of each finding as
requirements of your review policy. A successful Actions job alone does not
prove Claude completed the review: inspect the authoritative `Claude Review`
Check and its captured patch identity. A completed `action_required` Check
records findings; assess each one and record whether it was fixed, invalid, or
explicitly declined by the responsible maintainer. Disposition does not rewrite
the Check or erase recorded findings. Material patch changes need fresh review.

Requiring a finding-free `Claude Review` Check on every current head is a
stricter branch-protection choice. It can block a review whose findings have
legitimate dispositions, or evidence carried forward under a policy that allows
verified integration-only updates. Such carry-forward requires confirming that
the effective reviewed patch is unchanged and the base changes neither introduce
material interactions nor invalidate the review. Record the reviewed revisions
and that assessment; a new commit, a small diff, or a docs-only change does not
automatically qualify. The runtime does not perform this policy assessment or
transfer an old Check to the new head.

Missing, incomplete, or stale Claude review evidence is not satisfied by another
reviewer's approval or a green Actions job. Verify coverage and obtain a fresh
Claude review when required. See the [review-result contract](review-guide.md#trust-and-review-results)
for captured-patch authority, finding retention, and stale presentation.

## Prerequisites

This runtime repository is public. Consumers can reference its reusable
workflows by commit SHA, subject to their GitHub Actions policies. Publication
does not validate a new consumer configuration; the tested boundaries are listed
below and in the [validation summary](validation.md).

Choose a runtime branch and its model authentication secret:

| Branch | Actions secret |
| --- | --- |
| `main` | `CLAUDE_CODE_OAUTH_TOKEN` |
| [`api-key`](https://github.com/JBallin/claude-review-runtime/tree/api-key) | `ANTHROPIC_API_KEY` |

The `api-key` branch has not been live-API-tested; it does not require OAuth.
`main` does not accept API keys. Offline checks do not establish validated API-key
support. See the
[branch comparison](https://github.com/JBallin/claude-review-runtime/compare/main...api-key)
for source differences.

Install the [Claude GitHub App](https://github.com/apps/claude) with access to the
consumer repository. The app provides GitHub access separately from the model
secret above. Custom GitHub authentication is unsupported by this runtime.

The supported platform is GitHub.com with GitHub-hosted Ubuntu runners. The
runtime-local helper action uses `$/actions/review-helper`, which requires runner
version 2.336.0 or later. Broader public-consumer coverage, other-owner, fork, and
GitHub Enterprise Server configurations remain unvalidated. See the
[validation summary](validation.md) for exercised configurations and coverage
limits. The runtime is licensed under the [MIT License](../LICENSE).

## Install the callers

Install three separate workflow files in the consumer repository. Replace
`<full-reviewed-runtime-sha>` with one reviewed 40-character commit SHA from your
chosen runtime branch in all three files. Changing only the secret name while
keeping a `main` commit does not enable API-key authentication.

The automatic and manual examples below use `main`'s OAuth secret. For `api-key`,
use a reviewed commit from that branch and replace each review caller's `secrets`
block with:

```yaml
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Forward only the chosen branch's named secret, not both. The status caller needs
no model secret. Have the caller installation reviewed by a human and merged to
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

For the current pinned self-review callers, post `/claude-review` as the entire comment,
without whitespace or arguments; matching is case-insensitive. This repository’s
self-review callers use that same command with their reviewed runtime pin. Any additional manual
caller gate must match the selected runtime revision.

### Optional model and effort selection

The reusable manual entrypoint in this source revision accepts these entire
comment bodies, case-insensitively, with exactly the spaces shown:

```text
/claude-review
/claude-review opus
/claude-review effort high
/claude-review opus effort high
```

`effort` accepts only `low`, `medium`, `high`, `xhigh`, or `max`. No surrounding
whitespace, extra arguments, model IDs, CLI flags, or other models are accepted.
Selection comes only from the trusted comment; there is no reusable-workflow
model or effort input. Additional caller eligibility gates must allow exactly
these forms for their chosen Runtime revision. The current self-review pins and
their plain-command gate remain unchanged; adopting the new contract is separate.

Plain invocation supplies neither override and does not guarantee Sonnet.
`opus` selects Claude Code's alias, whose resolution can change with upstream
versions and trusted configuration. The preflight rejects a conflicting ambient
`ANTHROPIC_MODEL` before invoking an explicit Opus review. Authentication and
model entitlement still depend on the existing OAuth account; selection grants
no access. Provider failures retain their ordinary incomplete-review behavior.

Higher effort can increase usage and latency. Existing turn and time limits are
not a spending cap. The requested level may be limited by the model or account
policy. The pinned headless integration does not report effective effort, so
presentation labels effort as **requested** and observed effort as **unknown**.
No private reasoning text is exposed.

The comment shows requested overrides and available model evidence only when a
model or effort override was requested. Plain requests omit selection metadata
from both the summary and Details; diagnostic evidence remains available. Missing,
mixed, or different model evidence is informational: it never invalidates an
otherwise verified review or erases findings. Check Details before deciding
whether a completed review satisfies your intended model choice. There is no
additional automatic retry or substitute-model run. Underlying Claude Code may
perform its own retries or fallbacks; observed models describe the execution
record, not a guarantee about every internal model call.

The runtime resolves the current patch once for each accepted manual request.
Ordinary issue comments and untrusted requests do not start model execution.

## Stale presentation refresh

```yaml
name: Claude review status
on:
  pull_request:
    types: [synchronize, edited]
  push:
    branches: ["**"]

permissions: {}

jobs:
  status:
    permissions:
      contents: read
      pull-requests: write
      issues: write
    uses: JBallin/claude-review-runtime/.github/workflows/claude-review-status.yml@<full-reviewed-runtime-sha>
```

This entrypoint needs no model secret, OIDC permission, or Check write permission.
It refreshes existing presentation after head changes and qualifying base
retargeting edits and pushes to their target branch. It does not create a review
when none exists or rerun Claude. Completed review evidence and exact-patch Checks
remain unchanged; the existing status explains coverage of the newer baseline.

Target pushes discover only open PRs with that exact base branch, including
stacked PRs on non-default branches. Discovery is read-only and bounded to 100
pages of 100 PRs and 120 seconds. More than 256 affected PRs or incomplete discovery
fails without emitting a partial job matrix. At most four refresh jobs run in
parallel, each sharing its PR's review queue. Delayed pushes, closed or retargeted
PRs, and changed presentation owners are rechecked before publication. Status API
failures remain best effort and appear as workflow warnings; they never change a
Check verdict. A later qualifying event can retry a missed refresh.

Existing consumers must separately update their status caller to include the
`push` trigger and select a reviewed runtime revision. Updating a runtime pin
alone cannot add caller events. This repository's pinned self-review callers
also require separate adoption; this implementation does not repin them.

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
