# Validation and limitations

The historical live results below exercised OAuth and do not validate the
experimental API-key branch. That branch has not been live-API-tested; offline
checks do not establish live authentication, model completion, or publication.
See the [shared setup guide](consumer-workflows.md) for branch selection.

## Current offline evidence

[PR 22](https://github.com/JBallin/claude-review-runtime/pull/22) added test-only
restoration and publication-recovery regressions. Reviewed candidate
`fb656482b0070fd382bc058901c200f7899b7542` passed all 319 tests in 291.222 seconds;
the six focused methods passed in 8.034 seconds. CI and independent review passed.
The merge at `90c4287d88cae251232e16aac9af896c604f7163` has the identical complete
tree, `62355d659a6425512ca4dd0aa78519b7f5f69ca8`. These are offline test results
and review of the test patch, not a new live runtime trial or caller adoption.

The new methods cover 32 parameterized cases across four groups:

- Twelve actual head/base A → B → A restorations across automatic, top-level,
  and inline requests, with and without a newer accepted owner. Restored identity
  follows the existing exact-match policy; a newer owner blocks the old writer.
- Process restart before and after durable Check, status, notice, and owner
  commits, plus synthetic missing or partial finding-receipt evidence.
- Nine finite two-writer schedules at running, Check-committed, and completed
  boundaries, with at most twenty API events for each obsolete finalizer.
- A three-attempt simulated history outage followed by recovery and a drifting
  history count, plus an injected-clock check of the expired API deadline gate.

Separate helper processes share a persisted synthetic API backend. Missing or
ambiguous evidence stays non-clean, historical findings are retained, and a
superseded writer cannot replace the newer result in these schedules.

[Issue 13](https://github.com/JBallin/claude-review-runtime/issues/13) remains open.
The finding-receipt cases represent a durable comment plus lost or partial
receipt evidence; they do not execute or interrupt the finding publisher. The
deadline case tests an expired API gate, not lifecycle deadline setup or reset.
Finite schedules do not establish atomicity between separate API reads and
writes, actual runner cancellation or queue delivery, artifact transport,
real service outages, or sustained concurrent load. Broader live testing requires
its own approved fixture matrix, budget, retry limits, and restoration plan.

Additional deterministic schedules compose those boundaries on source revision
`09574d8e03259351798019f48ed57b6c15fecff4`:

- Six Check-commit → patch movement → stale refresh → restart cases retain the
  captured Check identity and suppress clean notices and reactions for the moved
  patch, across automatic, top-level manual, and inline manual requests.
- Three history-outage and lost-receipt cases delete the finding before a new
  attempt. The failed historical Check retains attribution; recovered history
  keeps the new result `action_required` without a surviving comment.
- Twenty reaction-publication cuts, immediately before or after a durable POST,
  recover across the opening and both manual endpoints. A newer accepted attempt
  blocks the obsolete finalizer; unrelated reactions survive both schedules.

These 29 cases use fresh helper processes and persisted synthetic API state.
They add no runtime behavior or live coverage. In particular, successful
instrumented provider interruption does not establish ordinary Actions
cancellation behavior; [the controlled result](https://github.com/JBallin/claude-review-runtime/issues/13#issuecomment-5988261454)
keeps that distinction explicit.

## Security scan evidence

A single standard Codex Security repository scan of
`90c4287d88cae251232e16aac9af896c604f7163` covered all 25 tracked files and
reported zero validated findings. The 319-test offline suite also passed on that
revision. This is bounded source assessment; it does not establish exhaustive
security coverage or validate live authentication and deployment configurations.

## What has been tested

A bounded private, same-personal-owner trial of frozen runtime candidate
`39ea6a5a67030cd50ab896576a76787d1b05c85b` passed with two fixtures and three model
runs. This supports the tested invocation, completion, and finding-publication
behavior in that configuration. It does not measure exhaustive reviewer quality.

[PR 7](https://github.com/JBallin/claude-review-runtime/pull/7) implements immediate
inline publication, a valid receipt for every attempted finding, and trusted
confirmation against comments on the captured PR/head. It merged as revision
`91f5b76f00800e05ac41f408b427dd60c639d906`. The publication code and
regression tests are byte-identical to the frozen candidate. The PR reports 238
offline tests passing on an earlier integrated head with an identical complete
tree; that is inherited offline evidence, not a live trial of the merge SHA.
Use a reviewed full runtime SHA when installing consumers; merging the runtime
does not update their callers.

## What is deployed

This repository’s three self-review callers use reviewed runtime revision
`250a7d6745285dcc7902b604aec9f62f5b204033`. Their manual gate accepts the exact,
case-insensitive `/claude-review` command from trusted PR commenters. This pin
includes the merged finding-publication contract and sanitized captured-read
coverage diagnostics from [PR 16](https://github.com/JBallin/claude-review-runtime/pull/16),
presentation ownership from [PR 11](https://github.com/JBallin/claude-review-runtime/pull/11),
audited Check-history validation from [PR 18](https://github.com/JBallin/claude-review-runtime/pull/18),
and direct-ref base freshness from [PR 20](https://github.com/JBallin/claude-review-runtime/pull/20).
All three pins, caller tests, and deployment guidance are updated together;
manual eligibility, events, permissions, and credentials are unchanged.

Diagnostics run without an opt-in flag when verification reports `verified` or
`captured_inputs_not_read`; earlier failures may have no counters. They report
only fixed metadata/diff labels, counts, content-shape enums, and partial-view
booleans.
They do not export contents, paths, transcripts, or raw errors, and do not alter
the strict completion guard.

The single approved diagnostic review of PR 11 completed with authoritative
[Check 110927696406](https://github.com/JBallin/claude-review-runtime/runs/110927696406):
reviewed head `f539aab61e72b6e281d816c1ccc30e5a857c0fad`, base
`9a20dcdab1081abce44ab4358996016bd3330cdc`, executing runtime
`da81f4b17c0142fec4b6996e6c03aa725394cb1a`. Metadata covered 12/12 lines and diff
2,116/2,116 lines, with zero missing/unparsed lines, zero finding attempts, and no
evidence errors. Its two earlier incomplete Checks remain preserved. This was
verified review completion of the candidate patch; it did not execute the
candidate ownership protocol.

The [integrated audit report](https://github.com/JBallin/claude-review-runtime/blob/8dbcd6d786c9485670ffaaac55af4aa390c70d22/docs/audit-issue-12.md)
records audit input `bf210316642eb37ec5bd040b70cc87275c0a64d3` and the focused
history-schema, nullable-metadata, count, and known-current-Check corrections.
The merged runtime tree is identical to reviewed candidate
`8f0c4359566cea9033652874b4b0bc9ce9e9ad08`, which passed the six focused
regressions and 294-test full/CI suite. Its authoritative
[Check 110967387622](https://github.com/JBallin/claude-review-runtime/runs/110967387622)
verified clean completion with zero evidence errors using executing runtime
`da81f4b17c0142fec4b6996e6c03aa725394cb1a`. These are source/offline and
candidate-review results, not a live exercise of the adopted runtime's
ownership behavior. No adversarial/stress execution is part of caller adoption.
The audit retains limits on internally consistent historical listings and the
API's 1,000-check-suite boundary; it does not prove atomic history completeness.

## Remaining boundaries

Confirmed consumer reviews used GitHub.com, hosted Ubuntu runners, and Claude
OAuth with default Anthropic App/OIDC authentication.

[Ballin's verified consumer evidence](https://github.com/JBallin/claude-review-runtime/issues/29#issuecomment-5974625017)
confirms one manual review in a public repository using the
[installed caller](https://github.com/JBallin/ballin-scripts/blob/729264f35ce52d5605a0b2681b762c85bbe4346e/.github/workflows/claude.yml)
and runtime `d63506dda127e0509346c19457c1d992292a9c29`. It does not validate
automatic triggers or failure paths. Broader public-consumer coverage, other
owners, fork behavior, and GitHub Enterprise Server remain unvalidated.
The automatic entrypoint excludes forks; the manual entrypoint has limited fork
handling, while this repository’s caller gate excludes them. These code paths
are not a claim of validated fork support.

Workflow access, runtime-action resolution, and runner preparation can fail
before the trusted helper starts, preventing it from publishing a Check.
App/OIDC validation can instead skip model execution while the Actions workflow
succeeds; the review remains incomplete and cannot establish clean completion.

[Issue 9](https://github.com/JBallin/claude-review-runtime/issues/9) tracks a
specific trusted diagnostic for that provider condition. The existing provider
flag is proposed as a caller-visible output in
[upstream PR 1879](https://github.com/anthropics/claude-code-action/pull/1879).
Contribution candidate `598e57ae7ad0df3a11f9da81d2b4965ac03bc6a5` passed a GitHub
Actions test of a real workflow-validation mismatch and output propagation.
It is not the runtime's
dependency pin. A supported upstream revision, reviewed immutable dependency
adoption, and tested runtime Check/status handling are still required. Until then,
unknown causes retain the generic incomplete, non-clean result. Missing execution
alone cannot identify a workflow-validation block, and no merge exception follows.

The observed default-branch validation rejection published a failed Check after
the trusted start job had created it. Inspect the workflow run and captured
runtime identity as well as the Check. Follow the review guide’s owner-exception
guidance for initial installation; a skipped model run does not satisfy required
review. Clean completion confirms the runtime’s execution and publication
contracts, not the absence of every possible defect. Keep independent review
requirements in place.

## Worked example

For a consumer using the merged publication contract, an accepted
`/claude-review` request captures one patch. A completed review with confirmed
inline findings produces
an action-required Check and retains those findings. A clean completed review
produces a successful Check and a historical completion notice; a clean rerun
of the same patch identity adds no duplicate notice. Unconfirmed publication
produces an incomplete review and no clean notice. After the head changes, the
status caller refreshes stale presentation; request a new review for the new diff.

This example describes the contract, not an additional live test.
