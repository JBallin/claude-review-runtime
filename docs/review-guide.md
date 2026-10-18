# Review behavior and repository self-review

*Use this guide to interpret review results, operate this repository's pinned callers, and handle installation and caller updates.*

## Pinned callers

The three self-review callers below pin reviewed runtime code. They leave the
reusable entrypoints separate and keep publication code pinned even when a PR
changes the runtime.
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

The exact-SHA `Claude Review` Check is authoritative. The stable workflow-owned
status comment and opening-post reactions present progress, clean completion,
findings, failure, and stale state. One shared status comment shows the latest
accepted attempt, including its captured head/base and workflow run. Checks and
workflow runs retain earlier attempts. Incomplete and cancelled reviews include
a reason inside Details. An incomplete presentation also includes the general
workflow-change note; it is context about a known validation limitation, not a
diagnosis of that attempt. A comment API or permission failure is reported in
the Check/run when publication is possible. Accepted manual requests also receive
runtime reactions on their top-level or inline trigger comment: 👀 while running and 👍
only after verified clean completion and successful authoritative Check
publication. For a manual review, 👍 means the captured patch review completed
without findings; it establishes neither integration coverage nor merge approval.
It can remain after the head or base changes; it does not establish review
coverage of the current version. The status comment identifies the reviewed commit, preserves
its completion time when available, and shows the current coverage caveat.
The running status shows a live relative start time when available, measured
from trusted start-job preparation rather than confirmed model execution.
Refreshes preserve it; a new attempt uses its own start time.
Successful and findings Check summaries display their recorded completion time.
Automatic reviews react only to the opening post. Running eyes can
appear while Check publication is unavailable; they indicate an active attempt,
not a review result. Findings, failure, cancellation, unverified completion, and
terminal unavailable Check remove the runtime's eyes/thumbs-up pair best-effort.
Ambiguous completion evidence also removes 👍; an older clean result never
restores it over a newer running, findings, or incomplete attempt.
Stale refreshes retain an existing historical 👍 but do not recreate a removed one.

Manual admission distinguishes the captured comparison base from the directly
resolved target branch tip. It validates repository/ref authority and unchanged
head and target ref across PR reads surrounding the direct-ref lookup. Valid
comparison-base projections and target tips may differ from the captured base;
missing, malformed, foreign, ambiguous, or inconsistent authority still rejects
the request, including a manual fork request. Admission does not require an
ancestry relationship between valid tips on the same ref. The reviewed patch
remains the captured `compare/base...head` payload, with its captured merge base
and runtime provenance; admission never replaces that baseline or synthesizes a
merge. Automatic reviews retain the triggering event's immutable head/base and
historical Check identity.

After a verified clean manual review and successful Check publication, trusted
finalization may add 👍 and a completion notice even when the validated target
tip differs from the captured baseline. The notice identifies its captured head,
base commit, and workflow run, and qualifies integration coverage. Automatic
reviews update the shared status comment without adding a separate completion notice.
Reserve this runtime's namespaced comment markers for its trusted publication jobs.

Completion notices are historical information, not review authority or merge
approval. There is at most one notice per PR/head/base-commit/base-ref/merge-base
identity. Repeating the command still reviews the patch and refreshes Check/status
state, but a clean rerun of the same identity adds no notice. Recorded findings or
a failed rerun leave prior notices unchanged; consult the current Check and status.
A newly captured comparison base or target ref creates a different notice
identity even when the head is unchanged; observing a different target tip alone
does not.

No success notice is posted for recorded findings, failure, unverified completion,
or an unavailable Check. Publication verifies the executing attempt and persisted
clean owner, then rechecks ownership and validated live head/ref immediately
before posting or adding new clean reactions. Changed head/ref or a newer owner
blocks these signals. A valid target-tip difference alone does not. Notices and
Check summaries describe the captured baseline and exclude other target
baselines from coverage. A notice also identifies the target tip observed at
publication when it differs from the captured baseline.
A concurrent PR change can still occur afterward, so the notice describes its
linked historical snapshot.
Notice publication is best-effort: an API failure may leave no notice, and an
ambiguous POST is not retried blindly. Neither changes the authoritative result.

The pinned runtime’s clean result also requires
[confirmed finding publication](#finding-publication). Keep independent review
requirements in place.

## Permissions and authentication

This repository's installed self-review callers start with `permissions: {}`.
They use the pinned OAuth runtime and forward only `CLAUDE_CODE_OAUTH_TOKEN`,
only to the two review calls. Consumers follow the branch-specific secret
instructions in the [shared setup guide](consumer-workflows.md).

| Installed self-review job role | Workflow-token permissions | Model secret |
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

The status presents one row with Status, Commit, and Review trigger.
For a verified clean or findings result, Status shows its persisted completion time
and Commit identifies the reviewed SHA. The current-version or integration caveat
appears below the row; newer running, failed, or incomplete attempts remain prominent
above historical results. Technical details, including the accepted attempt's start
time, are collapsed. GitHub renders recorded times as relative times.
Stale status retains the last completed review's time; historical results without
a recorded completion time remain undated. These timestamps describe lifecycle
events, not a heartbeat or proof that a runner is still active. Failed, cancelled,
and incomplete attempts do not receive a review completion timestamp.

Review trigger retains the original accepted event that produced the completed
result: PR opened for review, Draft marked ready, or Manual request. Status refreshes
and repeated completion cannot replace or backfill that event. Legacy records without
trusted original-event metadata show Not recorded; automatic ownership alone cannot
distinguish opening a PR from marking a draft ready. Emergency fallback preserves
these historical markers for subsequent helper rendering.

Terminal reactions on older trigger comments remain historical snapshot
information when the patch changes or another comment requests a review. A new
accepted attempt on the same comment replaces its owned reactions. Superseded
attempts never add clean thumbs-up or overwrite another request's presentation.
If the captured head or base is already superseded at start, the latest accepted
owner shows a stale status instead of leaving its comment absent. If the head,
base ref, or base tip moves during a still-owned review, finalization marks the
shared status stale and clears progress reactions. Older attempts cannot replace
a newer owner's status. When a trustworthy completed result belongs to that
same owner and the head and target ref are unchanged, the comment keeps the
completed result prominent and notes that the target baseline differs. It
identifies the reviewed baseline separately from the current baseline, whose
integration has not been reviewed. Findings remain findings; changed heads or target refs,
incomplete attempts, and missing or ambiguous completion provenance retain the
stale presentation. Undated completed results remain undated. The machine
freshness state remains stale and the captured Check retains its original
authority. Trusted verified clean manual finalization may still add 👍, qualified
as captured-patch completion; automatic clean-signal behavior is unchanged.
Status-only refresh can retain a historical 👍 but cannot recreate a removed one.
Freshness checks read the exact encoded branch ref directly and validate its
ref name, commit type, and full SHA, with PR identity reads around that lookup.
A status refresh can observe this movement
even if the PR API still reports the old comparison base. This is not a watcher
for base-branch pushes, and separate API reads and writes are not atomic:
movement after the last read remains possible. Unavailable branch authority
suppresses new clean presentation rather than strengthening the Check result.
Stale refresh preserves ownership metadata and
affects only current PR presentation. Reaction and status publication are best-effort: API failures or
unverifiable ownership can leave stale or missing reactions. Consult the current
exact-head Check.

Completion requires every captured metadata and diff line to appear in successful
Read responses. Claude can read additional ranges after a partial response;
unread or truncated lines fail closed. Every inline-finding call must target the
captured head SHA, and errored responses reject completion even if the model
reports success.

Findings remain sticky for the same PR, head, and merge base through trusted
Check evidence. Captured target-ref evidence is additive; legacy Check records
without it remain readable and require no migration. Findings attributed only to
another diff do not carry across a base retarget; unknown attribution fails closed.
Check evidence retains bounded ID samples and diagnostic counts/hashes, plus
durable same-diff finding presence.
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

The reusable review entrypoints publish inline findings immediately. Trusted
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

Before live use, the owner must confirm the Claude GitHub App has access to the
consumer repository and provide the selected branch's model secret. Follow the
[shared setup guide](consumer-workflows.md) for authentication limits,
branch-specific pins and secret forwarding. This repository's installed
self-review callers continue to use OAuth.
Secret-name metadata verifies
presence, not credential validity or successful live App/OIDC execution. The
caller setup does not install an App or configure credentials. New credentials
or access grants require a separate decision.

Default Anthropic App/OIDC validation can reject a new or modified caller
workflow until it exists with matching content on the default branch. A rejected
caller may skip model execution even when Actions orchestration is green; the
trusted `Claude Review` Check remains incomplete or failed, and required review
remains unsatisfied. This condition does not mean every first run fails or every
PR changing a workflow is unreviewable.

Reconcile the automatic review's Check and workflow run before requesting
recovery. After a confirmed caller-validation rejection, use the already deployed
trusted manual caller only when no applicable review request is completed or
pending. Follow its documented command and verify the captured head, base, and
executing Runtime revision in the resulting Check and workflow run. A pin update
alone does not require manual review; this recovery applies to the confirmed
rejection, not every caller change.

A skipped action or successful workflow without verified model completion is
not a Claude review. Do not suppress the incomplete Check or bypass it. Keep an
initial setup draft while authentication and independent review are outstanding.
Any initial installation exception requires explicit owner approval, and the
owner merges the setup.

Captured-read diagnostics depend on the executing Runtime revision recorded in
the Check and workflow run. This repository's installed
[automatic](../.github/workflows/self-review-automatic.yml) and
[manual](../.github/workflows/self-review-manual.yml) self-review callers pin
`310ae2f`, which reports aggregate read and successful-read counts,
expected/matched/missing/unparsed line counts, content-shape labels, and a
partial-view boolean. Runtime
[`d85a8b2`](https://github.com/JBallin/claude-review-runtime/commit/d85a8b21653d2d3703829f9b49fce447acd57415)
added bounded per-Read requested offsets and limits, returned ranges and overlaps,
plus missing ranges and truncation flags. Those details are unavailable from the
installed `310ae2f` callers; caller adoption is separate from this guidance.

Both revisions report sanitized captured-read coverage diagnostics during
verification when the reason is `verified` or `captured_inputs_not_read`,
without an opt-in flag. Earlier failures may have no coverage counters. The
fixed input labels are `metadata` and `diff`. No file contents,
paths, transcripts, or raw errors are exported.
Diagnostics are best-effort and do not change acceptance: missing captured lines
still fail completion. A partial page can be followed by complete reads; consult
coverage and the authoritative Check together.

After the human-reviewed caller installation is merged, validate with an approved
live run on a separate PR that does not modify the workflows. Inspect actual
model completion, captured identities, runtime provenance, and posted findings.
An incomplete result does not approve the commit. Inspect the workflow run and
exact-head Check together; input capture, authentication, action resolution, or
runner preparation can prevent
model execution or publication. Resolve the reported prerequisite before
requesting another approved review; do not switch credentials or expand access
to bypass a rejection.

### Incomplete captured-input reads

For `captured_inputs_not_read`, check the executing revision first. With
`d85a8b2`'s diagnostics, inspect the reported gaps and unsuccessful Reads alongside
the recorded completion and configured limits. With `310ae2f`, aggregate counts
show incomplete coverage but cannot locate the gaps or failed requests. A
per-Read size limit does not establish total-context exhaustion. During model
execution, recovery requires continuing with `offset` at the first unread line
and a smaller `limit` if a page fails, until every captured diff line is covered;
`d85a8b2` records that total in `metadata.diff_line_count`.
A fresh approved review is not guaranteed to complete that
pagination; verify its own captured identities, coverage, and authoritative Check.

For example, [Ballin #580's manual attempt](https://github.com/JBallin/ballin-scripts/actions/runs/39157080846/job/117762535478)
used Runtime `d85a8b2`, Claude Code 2.1.289, Agent SDK 0.3.289, and
`claude-sonnet-5-5`. The [durable sanitized evidence record](evidence/ballin-580-pagination.md)
preserves the inspected snapshot's size, digest, identities, and reported coverage
without retaining its contents or transcript. The diff
was 298,487 bytes and 4,727 lines. A whole-file Read and
a 1,200-line request failed; four successful 600-line pages covered 1–2,400,
leaving 2,401–4,727 unread. The SDK reported normal success after 13 of 30 allowed
turns and about 50 seconds, with zero permission denials; the job's timeout was
20 minutes. The [exact-head Check](https://github.com/JBallin/ballin-scripts/runs/117762457169)
correctly rejected completion. These observations support early normal completion
with incomplete pagination, but do not explain why reading stopped. Tool errors,
the final assistant message, actual token consumption, and a model-level stop
reason were unavailable in the retained logs and artifacts.

Anthropic's [large-file guidance](https://code.claude.com/docs/en/tools-reference#large-files)
documents a 256 KB whole-file ceiling and per-call token limits. The captured
file exceeded that size ceiling, consistent with its initial failure; the exact
error and the cause of the 1,200-line failure remain unknown. The documented
`allow_large` option requires 2.1.296 or later, beyond the version used here.
[Issue #14876](https://github.com/anthropics/claude-code/issues/14876) reports a
25,000-token Read limit on 2.0.74; it does not prove this attempt's cause. The
[preceding automatic attempt](https://github.com/JBallin/ballin-scripts/actions/runs/39156658058)
reached line 2,499 with different page sizes, so neither 2,400 lines nor 600 lines
per page is an established ceiling. Incomplete pagination does not establish that
all large diffs fail. Preserve full captured-input coverage and publication
safeguards when diagnosing or recovering a review.

### Unexpected Check grouping

GitHub may group `Claude Review` under another workflow name, such as
[CodeQL in this example](https://github.com/JBallin/claude-review-runtime/pull/40/checks),
without implying a dependency. The cause and a reliable fix are unconfirmed; use
the `Claude Review` Check for the result.

## Updating callers

Update the automatic, manual, and status callers and their contract tests together
to one reviewed immutable runtime commit compatible with the selected
authentication mode and documented manual command.

Let active and queued reviews finish before activating the update. Preserve
Checks, findings, notices, and unrelated reactions. Publishing runtime changes
does not automatically update callers.
