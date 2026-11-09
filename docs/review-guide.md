# Review guide

*Use this guide to interpret review results and recover incomplete reviews.
For installation, start with the [consumer setup guide](consumer-workflows.md).*

The result and trust contracts below describe the runtime source in this
revision. A consumer runs the commit pinned in its callers, which can differ
from the source revision being read. Check the executing runtime identity in the
Check and workflow run when interpreting behavior or diagnostics.

Use these sections for the task at hand:

- [Request a manual review](#request-a-manual-review)
- [Read the result](#read-the-result)
- [Understand permissions](#permissions-and-authentication) and [trust boundaries](#trust-and-review-results)
- [Recover an incomplete review](#installation-and-incomplete-reviews)
- [Update or roll back callers](#updating-callers)

## Pinned callers

The three self-review callers below pin reviewed runtime code. They leave the
reusable entrypoints separate and keep publication code pinned even when a PR
changes the runtime.
Reviews cover the captured PR diff and record its head, base, merge base, and
the executing runtime's repository, SHA, and workflow path.

| Caller | Events | Eligibility and behavior |
| --- | --- | --- |
| [Automatic](../.github/workflows/self-review-automatic.yml) | PR `opened`, `ready_for_review` | Open, non-draft, same-repository PRs; Dependabot-triggered runs are excluded. |
| [Manual](../.github/workflows/self-review-manual.yml) | Top-level or inline PR comment `created` | Trusted human comment containing the command keyword, followed by a read-only check for an open, non-draft, same-repository PR. Runtime parses command lines and verifies the original author’s current access. |
| [Status](../.github/workflows/self-review-status.yml) | PR `synchronize`, `edited` | Open, same-repository PRs; refreshes existing presentation after head changes or base-ref retargeting. |

Stacked PRs are eligible; the callers do not restrict the base branch. The pinned
status caller does not run Claude, change the authoritative exact-head Check, or
watch base-tip advancement on the same branch. Request a new review when the
diff changes, and assess whether a changed base invalidates previous evidence.

## Request a manual review

Post `/claude-review` or one of its optional forms on its own line in a top-level
or inline PR comment. Surrounding whitespace and prose on other lines are allowed;
quoted and fenced examples are ignored. Matching is case-insensitive.

The commenter must have an owner, member, or collaborator association and current
write or admin permission on the repository. Runtime verifies the original
comment author, including on reruns, before admission, before starting the
review, and before publishing results. Missing or unavailable permission evidence
suppresses trusted Check, status, and reaction changes, including emergency
publication. Access can change between these separate API checks and writes;
they are not atomic.

The action also checks actor/access eligibility, and the pinned caller performs
its own eligibility check. Ordinary issue comments and untrusted requests do not
start model execution. Runtime classifies command lines before review starts
and can post usage guidance without checking out PR code. It resolves the
current patch once for each accepted request.

Short model and effort selections include
`/claude-review opus`, `/claude-review high`, and `/claude-review opus high`.
Models are Opus, Sonnet, or Haiku; effort is Low, Medium, High, Xhigh, or Max.
See [model and effort defaults](consumer-workflows.md#model-and-effort-defaults)
and [manual command forms](consumer-workflows.md#optional-model-and-effort-selection).
Model evidence is diagnostic: a different primary model produces a notice,
without changing completion or finding verification.

Use `/claude-review` to request a Runtime review. The mention `@claude review`
is not a Runtime command and does not start its manual workflow or installed
callers.

### Check the installed caller's command forms

This repository's [manual caller](../.github/workflows/self-review-manual.yml)
follows the [canonical setup](consumer-workflows.md#manual-review): it forwards
candidate text and lets the pinned Runtime classify command lines. Its supported
forms therefore come from that immutable revision, including commands with
surrounding whitespace or prose on other lines. Older consumer callers may have
stricter filters; check both the installed caller and its Runtime pin before
assuming source-revision syntax or usage feedback is available.

## Read the result

### Check the authoritative result

Open the `Claude Review` Check for the exact commit SHA you want assessed. That
Check is authoritative; the status comment and reactions help you follow progress.

| Result | What it means |
| --- | --- |
| Clean completion | The captured patch review completed with no recorded findings. This is not merge approval or a guarantee that every bug was found. |
| Findings | Assess the recorded findings. They remain part of the review history even after comments are deleted or a rerun fails. |
| Incomplete, failed, or cancelled | The attempt does not establish a completed review. Follow its explanation and [recovery guidance](#installation-and-incomplete-reviews). |
| Stale presentation | The patch or target has changed. Read the coverage caveat and decide whether a fresh review is required. |

The shared status comment shows the latest accepted attempt and links its
captured head/base and workflow run. Checks and workflow runs retain earlier
attempts. Expand an incomplete or cancelled review for diagnostics and general
troubleshooting and any specific recovery steps. Coverage warnings stay visible.
Verified unread inputs take precedence over workflow-change context. Details
retain the reason receipt and captured attempt identity. A comment API or
permission failure appears in the Check or run when publication is possible.

### Read Details and model warnings

Each run's Details shows its resolved **Model** and **Effort**, followed by its
baseline and Runtime provenance. Family labels are Sonnet, Opus, or Haiku; full
execution identifiers remain in recorded metadata. Current and previous attempts
each retain their own settings. A refresh neither applies today's defaults to
an older review nor fills missing historical settings.

Current model warnings stay visible when the summary is collapsed. If an attempt
is already incomplete, missing model evidence is shown in Details. A previous
run's recorded model mismatch appears inside Previous completed review, before
that run's settings, and uses only its captured evidence. Model warnings do not change completion or
finding verification.

For defaults, precedence, and rejected settings, see
[model and effort configuration](consumer-workflows.md#model-and-effort-defaults)
and [invalid manual requests](consumer-workflows.md#invalid-requests).

### Interpret reactions

Accepted manual requests receive 👀 on their top-level or inline trigger comment
while running, and 👍 only after verified clean completion and successful
Check publication. Automatic reviews react only to the PR opening post. Running
eyes can appear when Check publication is unavailable; they indicate an attempt,
not a review result.

For manual reviews, 👍 means the captured patch completed without findings. It
does not establish integration coverage or merge approval, and it can remain
after the head or base changes. Consult the status comment's reviewed commit and
coverage caveat.

Findings, failure, cancellation, unverified completion, or terminal unavailable
Check publication remove Runtime's eyes/thumbs-up pair on a best-effort basis.
Ambiguous completion also removes 👍. An older clean result cannot restore it
over a newer running, findings, or incomplete attempt. A stale refresh may retain
an existing historical 👍 but cannot recreate a removed one.

### Read timestamps and history

The collapsed **Claude Review** summary shows the latest outcome. Coverage
warnings stay visible, including when the target changes after a review completes.
Expand the summary to see **Status**, **Commit**, and **Review trigger**. A
completed review shows its recorded completion time and reviewed SHA.
Newer running, failed, or incomplete attempts remain prominent; previous
completed evidence moves inside Details, with links grouped by attempt.

GitHub renders recorded times relatively. A running time starts at trusted
start-job preparation, which does not confirm model execution. Refreshes preserve
it; a new attempt gets its own time. Stale status retains the last completed
review's time, and successful or findings Check summaries show that time too.
Historical results without a recorded completion time stay undated. Failed,
cancelled, and incomplete attempts receive no review completion time. These are
lifecycle timestamps, not a heartbeat proving the runner is active.

### Understand the reviewed baseline

Runtime reviews the captured comparison `base...head` diff and records its merge
base. It does not synthesize a merge with the current target branch. The target
branch's tip can differ from that captured baseline; a completed review does not
establish that integration with the current target was reviewed.

Manual requests validate the current head and target branch before admission.
Automatic reviews retain the triggering event's captured head/base and Check
identity. See [patch and target safeguards](#captured-patch-and-target-branch)
for how Runtime validates those identities.

### Manual completion notices

After a verified clean manual review and successful Check publication, trusted
finalization may add 👍 and a completion notice even when the validated target
tip differs from the captured baseline. The notice identifies its captured head,
base commit, and workflow run. When the observed target differs from the reviewed
baseline, a visible warning explains that integration with the observed target
was not reviewed. Details link to that target snapshot. The standing status
comment reflects coverage of the current target. Automatic reviews update the
standing comment without adding a separate completion notice.

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
blocks these signals. A valid target-tip difference alone does not.

Notices and Check summaries describe the captured baseline. A notice uses “Reviewed against”
for that baseline and “Observed target commit” for the target snapshot at
publication when it differs from the reviewed baseline.
A concurrent PR change can still occur afterward, so the notice describes its
linked historical snapshot.

Notice publication is best-effort: an API failure may leave no notice, and an
ambiguous POST is not retried blindly. Neither changes the authoritative result.

A clean result also requires [confirmed finding publication](#finding-publication).
Keep independent review requirements in place.

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

This section explains the safeguards behind the [results and signals above](#read-the-result).

### Model access and runtime identity

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

### Which attempt can update presentation

Only an accepted trusted start acquires current presentation ownership under the
shared PR queue. Its persisted owner binds the run/attempt, trigger target, and
captured patch. Finalization and emergency repair must match that owner and the
executing attempt; a partial rerun cannot claim it. A newly executed start on a
rerun can replace current presentation, including on the same head. Missing or
unverifiable owner evidence suppresses presentation writes. A start can establish
ownership on a legacy status; finalization cannot. Duplicate starts do not reset
a completed result or reclaim presentation from a newer request.

Review trigger identifies the accepted event for the attempt or completed
result shown in the main table: PR opened for review, Draft marked ready, or
Manual request. A current-attempt receipt binds that event to its exact owner
and generation, separately from completed-review history. Status refreshes
and repeated completion cannot replace or backfill that event. Legacy records without
trusted original-event metadata show Not recorded; automatic ownership alone cannot
distinguish opening a PR from marking a draft ready. Emergency fallback preserves
verified current-attempt and historical markers for subsequent helper rendering.

### Captured patch and target branch

Manual admission distinguishes the captured comparison base from the directly
resolved target branch tip. It validates repository/ref authority and unchanged
head and target ref across PR reads surrounding the direct-ref lookup. Valid
comparison-base projections and target tips may differ from the captured base;
missing, malformed, foreign, ambiguous, or inconsistent authority still rejects
the request. Admission does not require an ancestry relationship between valid
tips on the same ref. The reviewed patch remains the captured
`compare/base...head` payload, with its captured merge base
and runtime provenance; admission never replaces that baseline or synthesizes a
merge. Automatic reviews retain the triggering event's immutable head/base and
historical Check identity.

### When the patch or target changes

Terminal reactions on older trigger comments remain historical snapshot
information when the patch changes or another comment requests a review. A new
accepted attempt on the same comment replaces its owned reactions. Superseded
attempts never add clean thumbs-up or overwrite another request's presentation.
If the captured head or base is already superseded at start, the latest accepted
owner shows a stale status instead of leaving its comment absent.

If the head,
base ref, or base tip moves during a still-owned review, finalization marks the
shared status stale and clears progress reactions. Older attempts cannot replace
a newer owner's status.

When a trustworthy completed result belongs to that
same owner and the head and target ref are unchanged, the comment keeps the
completed result prominent and identifies the reviewed baseline separately from
the current target commit observed at the last refresh, whose integration has
not been reviewed. The target branch may have advanced, rewound, or been rewritten;
this label does not establish live freshness or forward ancestry.

Findings remain findings; changed heads or target refs,
incomplete attempts, and missing or ambiguous completion provenance retain the
stale presentation. Undated completed results remain undated. The recorded
freshness state remains stale and the captured Check retains its original
authority. Trusted verified clean manual finalization may still add 👍, qualified
as captured-patch completion; automatic clean-signal behavior is unchanged.
Status-only refresh can retain a historical 👍 but cannot recreate a removed one.

Freshness checks read the exact encoded branch ref directly and validate its
ref name, commit type, and full SHA, with PR identity reads around that lookup.
A status refresh can observe this movement
even if the PR API still reports the old comparison base. Separate API reads
and writes are not atomic: movement after the last read remains possible.
Unavailable branch authority suppresses new clean presentation rather than
strengthening the Check result.

Stale refresh preserves ownership metadata and affects only current PR
presentation. Reaction and status publication are best-effort: API failures or
unverifiable ownership can leave stale or missing reactions. Consult the current
exact-head Check.

### Completion and retained findings

Completion requires every captured metadata and diff line to appear in successful
Read responses. Claude can read additional ranges after a partial response;
unread or truncated lines fail closed. Every inline-finding call must target the
captured head SHA, and errored responses reject completion even if the model
reports success.

Findings remain sticky for the same PR, head, and merge base through trusted
Check evidence, including when a partial rerun reuses an existing Check or
recovery records a failure. If that Check's evidence cannot be verified safely,
the attempt fails without replacing it; inspect the failed workflow alongside
the retained historical result.

Emergency Check evidence includes a `recovery`
record identifying the publishing run/attempt and failure. Its
`retained_evidence_scope: historical` labels the other evidence fields as retained
history, including execution verdicts and model observations; they do not verify
the recovery attempt. `none` means no historical evidence was reused. Recovery
does not fill missing legacy execution provenance.

Captured target-ref evidence is additive; legacy Check records without it remain readable and require no migration. Findings
attributed only to another diff do not carry across a base retarget; unknown
attribution fails closed.

Check evidence retains bounded ID samples and diagnostic counts/hashes, plus
durable same-diff finding presence.
Hashes cannot establish individual ID membership: rediscovered IDs omitted from
the samples fail closed if no other trusted record attributes them. Raw model
transcripts are not uploaded as artifacts. Dependency actions are pinned, but
this does not make their transitive runtime dependencies immutable.

### Eligibility and status refreshes

The reusable automatic entrypoint excludes drafts, fork pull requests, and
Dependabot-triggered runs. The Dependabot exclusion reflects [GitHub's Actions
secret restrictions for Dependabot-triggered workflows](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-on-actions).
The actor triggering the run is distinct from the PR author: a new trusted human
`/claude-review` comment on an open, non-draft, same-repository Dependabot PR can
pass this repository's manual caller gate. Re-running a Dependabot-triggered
run does not change its secret restrictions. The manual caller rejects fork PRs
regardless of commenter trust. The generic reusable manual entrypoint has a
broader top-level PR-comment path for forks, with best-effort Check publication;
that path remains unvalidated and does not establish supported fork operation.

The status entrypoint refreshes existing presentation after head changes, base-ref
retargeting, or target-branch pushes forwarded by its caller; it never initiates
a model review. This repository's pinned status caller has not adopted push
refreshes. Consumers need a separate [caller update](consumer-workflows.md#stale-presentation-refresh)
to enable them and must assess whether a changed base invalidates previous review
evidence.

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

### Inline finding publication failed

When the trusted verifier reports an inline-comment tool failure, the review
remains incomplete even if the action and workflow succeeded. Assess any posted
findings; they remain recorded independently of this attempt's completion.
The status links to that attempt's Check and shows the number posted only when
trusted discovery and the pre-review snapshot establish it. An unavailable count
does not mean zero findings. Repeated finalization preserves an established
count for the same attempt after comment deletion; legacy or conflicting count
evidence stays unknown. This category does not establish the underlying
provider, network, or authentication cause.

For a final candidate with no applicable review in progress, request one approved
fresh full review through the deployed [manual caller](#request-a-manual-review).
Verify its captured patch and completion in the resulting Check. Rerunning only
the publisher cannot repair missing model or tool completion. If the failure
repeats, stop and investigate the permitted upstream evidence; this guidance
does not authorize broader credentials, logging, or access. Earlier failed Checks
and posted findings remain historical evidence.

### A new or changed workflow did not run Claude

Default Anthropic App/OIDC validation can reject a new or modified caller
workflow until it exists with matching content on the default branch. A rejected
caller may skip model execution even when Actions orchestration is green; the
trusted `Claude Review` Check remains incomplete or failed, and required review
remains unsatisfied. This condition does not mean every first run fails or every
PR changing a workflow is unreviewable.

The pinned action does not expose a structured workflow-validation result, so
Runtime cannot confirm this cause automatically. [Issue #9](https://github.com/JBallin/claude-review-runtime/issues/9)
tracks the limitation; [claude-code-action #1879](https://github.com/anthropics/claude-code-action/pull/1879)
proposes the missing signal.

When the captured patch changes a relevant review workflow, expand Details
to see the possible cause and the link to the attempt logs. Unrelated workflow
changes do not trigger this explanation. Unavailable or ambiguous evidence
gets a general incomplete status.

Before requesting recovery:

1. Check the authoritative Check, workflow run, and attempt logs together.
2. Confirm that no applicable review request is completed or pending.
3. If the repository has a trusted manual caller deployed on its default branch
   and the PR is eligible, an authorized user can post its supported
   [`/claude-review` command](#request-a-manual-review).
4. Verify the captured head, base, and executing Runtime revision in the new
   Check and workflow run.

A pin update alone does not require manual review, and a manual request is not
guaranteed to succeed.

### Identify the Runtime that actually ran

Distinguish the PR's proposed pin from the caller and Runtime actually executing
each attempt. Status and manual receipt Details link the exact **Runtime** when trusted
provenance is available; unknown provenance is omitted. Status refreshes retain
the recorded attempt’s Runtime rather than substituting the refresher’s revision.
The Check's
`runtime_repository`, `runtime_sha`, and `runtime_workflow_path` retain the same
provenance. Inspect the linked run's caller and referenced workflows as needed.

Runtime metadata alone does not identify a pin-change recovery. Compare the
captured pin change with that attempt's executed revision before concluding
that the deployed Runtime ran instead of the proposed pin. Generic manual
receipts do not make this inference.

A deployed manual caller can execute its previously installed Runtime while
reviewing the proposed patch. A clean review through that older revision does
not establish live adoption of the proposed pin. Automatic and manual attempts
can execute different revisions; neither recovery nor use of the old revision
is guaranteed for every pin change or authentication setup.

### Validate an installation

A skipped action or successful workflow without verified model completion is
not a Claude review. Do not suppress the incomplete Check or bypass it. Keep an
initial setup draft while authentication and independent review are outstanding.
Any initial installation exception requires explicit owner approval, and the
owner merges the setup.

After the human-reviewed caller installation is merged, validate with an approved
live run on a separate PR that does not modify the workflows. Inspect actual
model completion, captured identities, runtime provenance, and posted findings.
An incomplete result does not approve the commit. Inspect the workflow run and
exact-head Check together; input capture, authentication, action resolution, or
runner preparation can prevent model execution or publication. Resolve the reported prerequisite before
requesting another approved review; do not switch credentials or expand access
to bypass a rejection.

### Turn-limit failures

Automatic and manual reviews omit an explicit turn cap and retain a 20-minute
review-job timeout. See [Execution limits](design-notes.md#execution-limits) for
the controls, evidence, and tradeoff.

When trusted execution evidence establishes a turn-limit error, the Check and
status explain it. Historical configured-cap attempts can also have SDK success
rejected by the Action's count comparison and remain incomplete. **Turns** in
collapsed Details and the Actions summary is the validated SDK-reported count
for that attempt, or unavailable. It does not measure API rounds, tokens, cost,
quota, or the native budget. Missing or malformed evidence does not
establish a specific cause, and a turn-limit diagnosis does not establish complete
input reading or satisfy review completion. Inspect the linked attempt before
requesting an approved retry.

### Incomplete captured-input reads

`captured_inputs_not_read` means the execution record did not verify complete
coverage of the captured metadata and diff. It does not establish why coverage is
incomplete. Inspect the exact-head Check and workflow diagnostics; successful
model execution or a green workflow is not sufficient.

Diagnostic availability depends on the executing Runtime revision recorded in the
Check. Older revisions may report aggregate coverage; newer diagnostic revisions
provide per-Read and missing-range details. These sanitized diagnostics do not
expose input contents or transcripts or relax completion requirements. See the
[preserved investigation](https://github.com/JBallin/claude-review-runtime/pull/75#issuecomment-6302262123)
for examples and diagnostic limits.

Recovery must preserve full-input coverage and publication safeguards. A fresh
approved workflow attempt is not guaranteed to succeed. If retries remain
incomplete, consider a separately run independent Claude review, such as local
Opus. Verify its input coverage and exact revision, record the results, and obtain
explicit acceptance under the applicable review policy. This does not make the
failed automated Check pass or bypass branch protection.

### Unexpected tool use

`unexpected_tool` means the execution record contains a tool-use name outside
the configured review allowlist. The workflow may log a best-effort
`Claude review unexpected-tool diagnostic` for the first such call. It emits
only the fixed labels `Agent`, `EndConversation`, `ScheduleWakeup`, or `Bash`,
with `other` for unrecognized strings and `malformed` for missing or non-string
names. `other` does not identify the tool. Arguments, outputs, raw unknown names,
and transcripts are excluded from this diagnostic.

Availability depends on the executing Runtime revision and diagnostic parsing;
an absent diagnostic proves nothing about tool use. A label identifies a recorded
name, not why it was called or whether it executed successfully. Diagnostics do
not change the failed verification result or recover a historical rejection's
cause when its execution evidence is unavailable.

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

### Roll back a caller update

If an adopted runtime revision causes problems, select a previously reviewed
commit compatible with the consumer's authentication mode and manual command.
Use the same three-caller update procedure above, including its contract tests
and active/queued-review precautions. Preserve historical Checks, findings,
notices, and unrelated reactions; restoring pins does not erase findings or
establish a new review result. Verify the executing runtime revision on the next
approved review.
