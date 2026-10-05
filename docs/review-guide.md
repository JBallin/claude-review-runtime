# Review behavior and repository self-review

*Use this guide to interpret review results, operate this repository's pinned callers, and handle installation or rollback.*

## Pinned callers

The three self-review callers use reviewed runtime commit
`250a7d6745285dcc7902b604aec9f62f5b204033`. They leave the reusable entrypoints
separate and keep publication code pinned even when a PR changes the runtime.
Reviews cover the captured PR diff and record its head, base, merge base, and
the executing runtime's repository, SHA, and workflow path.

| Caller | Events | Eligibility and behavior |
| --- | --- | --- |
| [Automatic](../.github/workflows/self-review-automatic.yml) | PR `opened`, `ready_for_review` | Open, non-draft, same-repository PRs; Dependabot-triggered runs are excluded. |
| [Manual](../.github/workflows/self-review-manual.yml) | Top-level or inline PR comment `created` | Trusted exact `/claude-review` command followed by a read-only check for an open, non-draft, same-repository PR. |
| [Status](../.github/workflows/self-review-status.yml) | PR `synchronize`, `edited` | Open, same-repository PRs; refreshes existing presentation after head changes or base-ref retargeting. |

Stacked PRs are eligible; the callers do not restrict the base branch. Status
refresh does not run Claude, change the authoritative exact-head Check, or
watch base-tip advancement on the same branch. Request a new review when the
diff changes, and assess whether a changed base invalidates previous evidence.

## Request a manual review

Post `/claude-review` as the entire top-level or inline PR comment, without
surrounding whitespace, arguments, quotes, or other text. Matching is
case-insensitive. The commenter must have an owner, member, or collaborator
association; the action also checks actor/access eligibility. Ordinary issue
comments and untrusted requests do not start model execution. The eligibility
job has read-only PR access and performs no checkout before calling the pinned
runtime. The runtime resolves the current patch once for each accepted request.

The command deliberately avoids a Claude mention. `@claude review` can invoke
Anthropic's separate managed review service and does not invoke this checkout's
manual entrypoint or this repository’s callers.
Provider-bot reactions on comments are preserved and are not this runtime's
progress or completion signals. The caller installation shape stays the same,
but a revision update must keep its manual gate and documented command consistent
with that revision.

The pinned runtime includes the trigger-comment reactions and presentation
ownership merged in [PR 11](https://github.com/JBallin/claude-review-runtime/pull/11)
and the fail-closed Check-history validation from
[PR 18](https://github.com/JBallin/claude-review-runtime/pull/18). See the
[validation summary](validation.md) for exact tested revisions and live limits.

The exact-SHA `Claude Review` Check is authoritative. The stable workflow-owned
status comment and opening-post reactions present progress, clean completion,
findings, failure, and stale state. Accepted manual requests also receive runtime
reactions on their top-level or inline trigger comment: 👀 while running and 👍
only after verified clean completion and successful authoritative Check
publication. Automatic reviews react only to the opening post. Running eyes can
appear while Check publication is unavailable; they indicate an active attempt,
not a review result. Findings, failure, cancellation, unverified completion, and
terminal unavailable Check remove the runtime's eyes/thumbs-up pair best-effort.

Runtime revisions containing the direct-ref freshness guard distinguish the
PR API's comparison-base SHA from the actual base branch tip. Before a manual
review starts, the captured PR head/base must still match the live PR, and its
comparison base must equal the directly resolved branch tip. Missing, malformed,
inconsistent, or outdated authority rejects that request before model execution;
the guard is required even for a manual fork request. It does not silently
replace the captured base or change the reviewed diff. Automatic reviews retain
the triggering event's immutable head/base and historical Check identity.

When the existing trusted result reports clean completion, trusted finalization
adds a fixed notice after successful Check publication, identifying its captured
head, base commit, and workflow run.
Reserve this runtime's namespaced comment markers for its trusted publication jobs.

Completion notices are historical information, not review authority or merge
approval. There is at most one notice per PR/head/base-commit/base-ref/merge-base
identity. Repeating the command still reviews the patch and refreshes Check/status
state, but a clean rerun of the same identity adds no notice. Recorded findings or
a failed rerun leave prior notices unchanged; consult the current Check and status.
A base change creates a different notice identity even when the head is unchanged.

No success notice is posted for recorded findings, failure, unverified completion,
or an unavailable Check. Publication checks the live head and base immediately
before posting and skips known superseded snapshots. A concurrent PR change can
still occur afterward, so the notice describes its linked historical snapshot.
Notice publication is best-effort: an API failure may leave no notice, and an
ambiguous POST is not retried blindly. Neither changes the authoritative result.

The pinned runtime’s clean result also requires
[confirmed finding publication](#finding-publication). Keep independent review
requirements in place.

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
checking out runtime code using a separate credential. Captured consumer
head/base identity binds the review to its patch; `job.workflow_repository`,
`job.workflow_sha`, and `job.workflow_file_path` identify the runtime executing
it. These are different provenance boundaries.

The exact-head Check is the review result. The stable comment and owned reactions
are informational presentation and can become stale when the patch changes.
Consumers must reserve `github-actions[bot]` eyes/thumbs-up reactions on PR
opening posts and accepted trigger comments for this runtime. Reconciliation
identifies the bot/reaction pair, not the individual workflow that added it;
human, Codex, provider, and other reaction pairs are preserved.

Only an accepted trusted start acquires current presentation ownership under the
shared PR queue. Its persisted owner binds the run/attempt, trigger target, and
captured patch. Finalization and emergency repair must match that owner and the
executing attempt; a partial rerun cannot claim it. A newly executed start on a
rerun can replace current presentation, including on the same head. Missing or
unverifiable owner evidence suppresses presentation writes. A start can establish
ownership on a legacy status; finalization cannot. Duplicate starts do not reset
a completed result or reclaim presentation from a newer request.

Terminal reactions on older trigger comments remain historical snapshot
information when the patch changes or another comment requests a review. A new
accepted attempt on the same comment replaces its owned reactions. Superseded
attempts never add clean thumbs-up or overwrite another request's presentation.
If finalization observes a base tip that advanced during a still-owned review,
it marks presentation stale and clears progress reactions. A verified completed
result remains historical evidence with its captured head, base commit, and
workflow run; the current baseline is shown as unreviewed. The captured Check
retains its original authority. Freshness checks read the exact encoded branch
ref directly and validate its ref name, commit type, and full SHA, with PR
identity reads around that lookup. A status refresh can observe this movement
even if the PR API still reports the old comparison base. This is not a watcher
for base-branch pushes, and separate API reads and writes are not atomic:
movement after the last read remains possible. Unavailable branch authority
suppresses new clean presentation rather than strengthening the Check result.
Stale refresh preserves ownership metadata and
affects only current PR presentation. Reaction and status publication are best-effort: API failures or
unverifiable ownership can leave stale or missing reactions. Consult the current
exact-head Check.

The self-review callers pin `250a7d`, which includes the direct-ref guard from
[PR 20](https://github.com/JBallin/claude-review-runtime/pull/20). Other consumers
retain their own pins until a separate reviewed adoption changes them.

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

## Finding publication

The publication contract below is implemented in merged runtime revision
`91f5b76f00800e05ac41f408b427dd60c639d906` and is included in this repository’s
pinned runtime. See the [validation summary](validation.md)
for the distinct live-tested revision.

Those reusable review entrypoints publish inline findings immediately. Trusted
completion requires a valid receipt for every inline attempt and confirms each
comment on the captured PR/head using the complete paginated comment list and pre-review
snapshot. Failed or ambiguous publication produces an incomplete review, retains
any findings already recorded, and cannot produce a clean completion notice.

The complete receipt list is limited to 64 finding attempts per run. Exceeding
that limit fails completion; receipt evidence is never truncated to imply clean
publication. The limit is separate from the sampled historical Check diagnostics.
An explicit zero-attempt receipt list permits clean completion only when all
other completion and finding-evidence checks pass. No additional consumer input,
credential, or permission is required.

## Installation and incomplete reviews

Before live use, the owner must confirm the existing Anthropic App has access
to this repository and make the existing OAuth credential available as the
repository Actions secret `CLAUDE_CODE_OAUTH_TOKEN`. Secret-name metadata verifies
presence, not credential validity or successful live App/OIDC execution. The
caller setup does not install an App or configure credentials. New credentials
or access grants require a separate decision.

Default Anthropic App/OIDC validation can reject a new or modified caller
workflow until it exists with matching content on the default branch. A rejected
caller may skip model execution even when Actions orchestration is green; the
trusted `Claude Review` Check remains incomplete or failed, and required review
remains unsatisfied. This condition does not mean every first run fails or every
PR changing a workflow is unreviewable.

A skipped action or successful workflow without verified model completion is
not a Claude review. Do not suppress the incomplete Check or bypass it. Keep an
initial setup draft while authentication and independent review are outstanding.
Any initial installation exception requires explicit owner approval, and the
owner merges the setup.

The pinned runtime reports sanitized captured-read coverage diagnostics during
verification when the reason is `verified` or `captured_inputs_not_read`,
without an opt-in flag. Earlier failures may have no coverage counters. For each
of the fixed `metadata` and `diff` labels, it reports read and successful-read
counts, expected/matched/missing/unparsed line counts, fixed content-shape labels,
and a partial-view boolean. No file contents, paths, transcripts, or raw errors
are exported. Diagnostics are best-effort and do not change acceptance: missing
captured lines still fail completion. A partial page can be followed by complete
reads; consult coverage and the authoritative Check together.

After the human-reviewed caller installation is merged, validate with an approved
live run on a separate PR that does not modify the workflows. Inspect actual
model completion, captured identities, runtime provenance, and posted findings.
An incomplete result does not approve the commit. Inspect the workflow run and
exact-head Check together; input capture, authentication, action resolution, or
runner preparation can prevent
model execution or publication. Resolve the reported prerequisite before
requesting another approved review; do not switch credentials or expand access
to bypass a rejection.

### Unexpected Check grouping

GitHub's PR checks view can group `Claude Review` under another Actions workflow
name, such as [`CodeQL` in this observed example](https://github.com/JBallin/claude-review-runtime/pull/40/checks).
This misleading grouping does not establish a dependency on that workflow.
Its cause and a reliable fix are unconfirmed; the `Claude Review` Check remains
the review result.

## Update or roll back

Updating all three pins to a reviewed revision is a separate change. Review and
merge that runtime revision first, and update the manual caller and invocation
guidance to match its interface. Keep caller-contract tests in sync with the
approved pin and interface. To roll back a pin update, restore all three
references and corresponding tests and deployment guidance together. For this
adoption, the prior pin is `8dbcd6d786c9485670ffaaac55af4aa390c70d22`; keep the
exact `/claude-review` gate unchanged because both revisions use it.

That rollback retains strict captured Read coverage, finding receipts,
sanitized diagnostics, presentation ownership, trigger reconciliation, and the
audited Check-history fixes. It removes the direct-ref freshness guard: the PR
API can retain an old comparison-base SHA after the branch advances, allowing
an outdated snapshot to be presented as current or admitted for manual review.
Rollback is an emergency disposition, not equivalent correctness or release
approval.

Before adoption or rollback activation, inspect active and queued reviews and
let existing attempts settle; do not silently cancel or rerun mixed-version
writers. Preserve Checks, findings, notices, and unrelated reactions. Any
necessary cleanup or ambiguous recovery needs a separately scoped decision.
Runtime publication does not update callers automatically.

To undo the initial self-review setup, revert the three callers, their focused
tests, and the associated documentation changes through a reviewed PR. Preserve
prior review evidence.
