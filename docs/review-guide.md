# Review behavior and repository self-review

*Use this guide to interpret review results, operate this repository's pinned callers, and handle installation or rollback.*

## Pinned callers

The three self-review callers use reviewed runtime commit
`9a9310f9ab8374f5c67bd9c0f067b9ff3dac3ebf`. They leave the reusable entrypoints
separate and keep publication code pinned even when a PR changes the runtime.
Reviews cover the captured PR diff and record its head, base, merge base, and
the executing runtime's repository, SHA, and workflow path.

| Caller | Events | Eligibility and behavior |
| --- | --- | --- |
| [Automatic](../.github/workflows/self-review-automatic.yml) | PR `opened`, `ready_for_review` | Open, non-draft, same-repository PRs; Dependabot-triggered runs are excluded. |
| [Manual](../.github/workflows/self-review-manual.yml) | Top-level or inline PR comment `created` | Trusted mention followed by a read-only check for an open, non-draft, same-repository PR. |
| [Status](../.github/workflows/self-review-status.yml) | PR `synchronize`, `edited` | Open, same-repository PRs; refreshes existing presentation after head changes or base-ref retargeting. |

Stacked PRs are eligible; the callers do not restrict the base branch. Status
refresh does not run Claude, change the authoritative exact-head Check, or
watch base-tip advancement on the same branch. Request a new review when the
diff changes, and assess whether a changed base invalidates previous evidence.

## Request a manual review

Create a top-level or inline PR comment containing `@claude`, for example:

```text
@claude review
```

Mention matching is case-insensitive and accepts additional text. The commenter
must have an owner, member, or collaborator association; the action also checks
the author's write access. Ordinary issue comments and untrusted comments do
not start review. The eligibility job has read-only PR access and performs no
checkout before calling the pinned runtime.

This baseline pin does not accept `/claude-review` or post a separate manual
clean-completion notice. The proposed exact `/claude-review` command and notice
require a separately reviewed runtime revision and caller update; changing the
documentation does not enable them. Manual results appear in the exact-head
Check and existing status presentation.

## Permissions and authentication

All callers start with `permissions: {}`. Only the named OAuth secret is
forwarded, and only to the two review calls.

| Job role | Workflow-token permissions | OAuth secret |
| --- | --- | --- |
| Automatic or manual review call | `contents: read`, `pull-requests: write`, `checks: write`, `issues: write`, `id-token: write` | `CLAUDE_CODE_OAUTH_TOKEN` |
| Manual eligibility | `pull-requests: read` | None |
| Status call | `contents: read`, `pull-requests: write`, `issues: write` | None |

The called runtime narrows the permission envelope independently: the model job
receives reads and OIDC, while trusted publication jobs receive writes. Its
per-PR queue owns serialization; do not add a caller lock using the same group.
For model and GitHub authentication prerequisites and App authority limits, see
the [consumer prerequisites](consumer-workflows.md#prerequisites) and
[trust boundaries below](#trust-and-review-results).

## Trust and review results

The Claude job receives repository and pull-request read permissions plus OIDC.
Trusted jobs create and finalize Checks and update status comments/reactions with
publication permissions. Claude's built-in tools are limited to Read, Glob, and
Grep; shell execution and file-editing tools are not enabled. Its inline-finding
tool is backed by the Anthropic App credential; the job's restricted
`GITHUB_TOKEN` permissions do not eliminate the App credential's own authority.

The helper action resolves from the reusable workflow's running commit, without
checking out private runtime code using a separate credential. Captured consumer
head/base identity binds the review to its patch; `job.workflow_repository`,
`job.workflow_sha`, and `job.workflow_file_path` identify the runtime executing
it. These are different provenance boundaries.

The exact-head Check is the review result. The stable comment and owned reactions
are informational presentation and can become stale when the patch changes.
Consumers must reserve `github-actions[bot]` eyes/thumbs-up reactions on top-level
PRs for this runtime: reconciliation identifies the bot/reaction pair, not the
individual workflow that added it.
Completion requires every captured metadata and diff line to appear in successful
Read responses. Claude can read additional ranges after a partial response;
unread or truncated lines fail closed. Every inline-finding call must target the
captured head SHA, and errored responses reject completion even if the model
reports success.

Findings remain sticky for the same PR, head, and merge base through trusted
Check evidence. Findings attributed only to another diff do not carry across a
base retarget; unknown attribution fails closed. Check evidence retains bounded
ID samples and diagnostic counts/hashes, plus durable same-diff finding presence.
Hashes cannot establish individual ID membership: rediscovered IDs omitted from
the samples fail closed if no other trusted record attributes them. Raw model
transcripts are not uploaded as artifacts. Dependency actions are pinned, but
this does not make their transitive runtime dependencies immutable.

The reusable automatic entrypoint excludes drafts, fork pull requests, and
Dependabot-triggered runs. Its manual entrypoint supports top-level fork requests
with best-effort Check publication limits; this repository's additional caller
gate excludes forks. Fork behavior remains unvalidated in this prototype. The
status entrypoint refreshes existing presentation after head changes or base-ref
retargeting; it does not initiate a model review or watch base-tip advancement
on the same branch. Consumers must assess whether a changed base invalidates
previous review evidence.

## Installation and incomplete reviews

Before live use, the owner must confirm the existing Anthropic App has access
to this repository and make the existing OAuth credential available as the
repository Actions secret `CLAUDE_CODE_OAUTH_TOKEN`. Secret-name metadata verifies
presence, not credential validity or successful live App/OIDC execution. The
caller setup does not install an App or configure credentials. New credentials
or access grants require a separate decision.

Default App/OIDC validation can reject a caller that does not exist with matching
content on the default branch. The verified rejection recorded in
[issue #9](https://github.com/JBallin/claude-review-runtime/issues/9) prevented
Claude from executing on the initial setup patch; required review remains
unsatisfied. This is a specific provider validation condition, not a rule that
every PR changing a workflow is unreviewable.

A skipped action or successful workflow without verified model completion is
not a Claude review. Do not suppress the incomplete Check or bypass it. Keep an
initial setup draft while authentication and independent review are outstanding.
Any initial installation exception requires explicit owner approval, and the
owner merges the setup.

The pinned revision has a known [finding-publication gap](https://github.com/JBallin/claude-review-runtime/issues/6):
attempted findings may not all reach GitHub even when its Check reports success.
Do not treat a green Check alone as merge approval or claim that every
publication failure is detected. Keep independent review requirements in place.

After an approved live run, inspect actual completion, captured identities,
runtime provenance, and posted findings. An incomplete result does not approve
the commit. Inspect the workflow run and exact-head Check together; input
capture, authentication, action resolution, or runner preparation can prevent
model execution or publication. Resolve the reported prerequisite before
requesting another approved review; do not switch credentials or expand access
to bypass a rejection.

## Update or roll back

Updating all three pins to a reviewed revision is a separate change. Review and
merge that runtime revision first, and update the manual caller and invocation
guidance to match its interface. Keep caller-contract tests in sync with the
approved pin and interface. To roll back a pin update, restore all three
references and the corresponding tests and guidance to the prior reviewed
revision. Runtime publication does not update callers automatically.

For an isolated trial, restrict caller eligibility to dedicated fixture branches
and prevent another reviewer from running on those fixtures. Restore the
consumer's original workflow configuration through a reviewed PR when the trial
ends. Keep fixtures and their evidence available for review.

To undo the initial self-review setup, revert the three callers, their focused
tests, and the associated documentation changes through a reviewed PR. Preserve
prior review evidence.
