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
    with:
      model: opus
      effort: high
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

The runtime preserves draft, fork, and Dependabot exclusions. Automatic review
captures the pull-request event's head and base; it does not silently move the
review to a later patch.

For the selected revision's publication behavior, see the
[finding-publication contract](review-guide.md#finding-publication).

## Workflow model and effort defaults

Set the optional `model` and `effort` inputs on each reusable-workflow caller.
The automatic and manual examples above and below use literal `opus` / `high`
values; choose the values you want and keep both callers consistent if they
should share defaults:

```yaml
with:
  model: opus
  effort: high
```

| Input | Supported values | Runtime fallback |
| --- | --- | --- |
| `model` | `opus`, `sonnet`, `haiku` | `sonnet` |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` | `medium` |

Values are case-insensitive and surrounding whitespace is ignored. Each field
resolves independently: a valid [manual selection](#optional-model-and-effort-selection)
overrides a valid workflow input, then Runtime falls back to **Sonnet / Medium**.
Runtime passes both resolved values to execution. An invalid manual option or an
invalid workflow input needed for resolution produces a warning and falls back
for that field only, retaining any independently valid selection. The warning
identifies the setting and replacement; diagnostic metadata records its source.
Correct the caller input or use a valid manual override on a later request.

Use literal owner-reviewed values in the caller, rather than expressions from
PR content. Runtime validates the resolved values; it cannot distinguish a
literal from a caller expression. It accepts only supported model and effort
values as execution arguments; inputs cannot configure prompts, tools, helper paths, credentials,
or review eligibility. Actions configuration variables are not consulted.
Settings are captured with each attempt, so later caller changes do not rewrite
its settings or historical results.

Default Anthropic App/OIDC validation can reject a new or modified caller
workflow until matching content exists on the default branch. A rejected caller
may skip model execution even when Actions orchestration is green; Runtime
requires verified completion and reports the review as Incomplete. See
[installation and incomplete reviews](review-guide.md#installation-and-incomplete-reviews)
for diagnosis and recovery.

These inputs require callers pinned to a Runtime revision that supports them.
Existing pins retain their prior behavior. Adopting this feature changes reviews
without configured defaults to explicit Sonnet / Medium; set caller inputs before
adoption if you want a different default.

## Manual review

Use this caller to accept manual review requests:

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
    with:
      model: opus
      effort: high
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

### Optional model and effort selection

Post one of these entire comment bodies, case-insensitively:

```text
/claude-review
/claude-review opus
/claude-review sonnet
/claude-review haiku
/claude-review effort high
/claude-review opus effort high
```

Any supported model alias can be followed by `effort` and one of `low`, `medium`,
`high`, `xhigh`, or `max`, such as `/claude-review sonnet effort high`. Selection
comes from the trusted comment; any caller eligibility gate must accept the
same forms as its selected Runtime revision. Full model IDs, context suffixes,
and routing modes are not supported model values. Callers with an older
Opus-only allowlist need a caller update as well as a new Runtime pin. Update
`uses:` to the reviewed Runtime revision, then replace the old model-specific
comment-body filter with the [manual caller template above](#manual-review).
That template delegates command recognition to Runtime, so it admits all three
aliases and lets malformed options reach the warning-and-fallback parser.
Preserve any additional access restrictions you require; do not remove
permissions or author checks to broaden access. Runtime still checks the event,
PR context, trusted commenter association, and captured patch authority before
execution. Review and merge this caller change through the normal consumer
adoption process; a pin update alone cannot change its filters.

Plain `/claude-review` uses both [workflow defaults](#workflow-model-and-effort-defaults),
falling back to Sonnet / Medium. A model-only command retains the resolved effort
default; an effort-only command retains the resolved model default.

For a recognized review command, an invalid model or effort value warns and
falls back to the valid workflow input, then the Runtime default, for that
field. Missing or malformed option values also show usage guidance. A malformed
option does not consume a following valid option, and independently valid
settings are retained. This recovery applies to configuration options, not
authorization checks, conflicting ambient configuration, or execution failures.
Explicit `model` / `--model` and `effort` / `--effort` option names are also
recognized, with a space or `=` before the value. For example,
`/claude-review --model --effort high` warns about the missing model value,
uses the workflow model or Sonnet, and keeps High effort. Duplicate options
fall back for that field and show usage guidance. Keep requests on one line,
without leading whitespace. Repeated or trailing spaces are accepted;
multiline comments, tabs, and unrelated extra text are not review requests.

The three aliases follow [upstream model resolution](https://code.claude.com/docs/en/model-config)
and can advance to newer versions. Model access, alias remapping, and effort
limits depend on the account and provider. Upstream may reject a selection or
clamp unsupported effort; Runtime does not silently choose a replacement.
A conflicting `ANTHROPIC_MODEL` or `CLAUDE_CODE_EFFORT_LEVEL` prevents the
review from starting. Higher effort can increase usage and latency; turn and
time limits are not a spending cap. See
[effort support](https://platform.claude.com/docs/en/build-with-claude/effort).

Resolved **Model** and **Effort** settings appear compactly inside Details for
automatic and manual reviews, including manual clean-completion receipts. Normal
model labels show the family name (Sonnet, Opus, or Haiku); full model identifiers
remain in recorded execution metadata. Historical results retain the settings
recorded for that attempt; missing historical settings are omitted. Effort is the
configured request, not an independently observed or effective value; headless
output does not report the resolved setting. See
[Claude Code #104936](https://github.com/anthropics/claude-code/issues/104936).

A selected model with missing or conflicting primary evidence, or execution by
a different model family, produces a visible warning outside Details. Auxiliary
model usage stays in diagnostic metadata and cannot confirm the primary model.
Legacy records without separate primary evidence retain the missing-evidence
warning. Model diagnostics do not invalidate completed reviews or erase
findings. Runtime adds no retries or substitution runs for these diagnostics;
provider failures retain their ordinary incomplete-review behavior.

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

For push events, [GitHub selects the workflow from the event's commit/ref](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows#workflow-triggers).
The updated status caller must be present on each target branch whose pushes
should trigger refreshes. Merging it into the default branch does not update
existing non-default target branches that still contain an older caller.

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
