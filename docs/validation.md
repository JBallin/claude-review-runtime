# Validation and limitations

Live evidence exercised OAuth. It does not validate the experimental API-key
branch, which has not been live-API-tested. Offline checks do not establish live
authentication, model completion, or publication. See the
[shared setup guide](consumer-workflows.md) for branch selection.

Choose validation from the effective upstream changes and gaps in available
evidence. Assess changes involving authentication, permissions, tool restrictions,
publication, or recovery for material risk; they do not automatically require a
separate review-lab trial. Propose a trial when offline checks and ordinary
verified adoption would leave material risk inadequately covered. Routine
compatible dependency updates may use required offline checks, independent
review, and verification of the actual Runtime revision executed by the next
normal review. Preserve fail-closed completion and publication gates and state
unverified behavior explicitly: a pin bump or a review using an older Runtime is
not candidate adoption evidence. Any live lab trial must follow the existing
[approval and bounded-run requirements](../CONTRIBUTING.md#change-scope).

## Offline evidence

[Publication regressions](https://github.com/JBallin/claude-review-runtime/pull/7),
[restoration and recovery tests](https://github.com/JBallin/claude-review-runtime/pull/22),
and the [integrated audit report](https://github.com/JBallin/claude-review-runtime/blob/8dbcd6d786c9485670ffaaac55af4aa390c70d22/docs/audit-issue-12.md)
record source review and offline validation. These results are not live trials
of the adopted runtime.

Deterministic tests use separate helper processes and persisted synthetic API
state to exercise restored patch identities, process restarts, competing writers,
history outages, and lost or partial receipts. Missing or ambiguous evidence
stays non-clean, historical findings remain sticky, and an obsolete writer cannot
replace a newer accepted owner's presentation in the tested schedules.

The [recovery evidence](https://github.com/JBallin/claude-review-runtime/issues/13#issuecomment-5988261454)
distinguishes controlled provider interruption from ordinary Actions cancellation.
Synthetic finding-receipt cases do not execute or interrupt the finding publisher;
the expired API deadline test does not validate lifecycle deadline setup or reset.
Finite schedules do not establish atomicity between separate API reads and writes,
actual runner cancellation or queue delivery, artifact transport, real service
outages, or sustained concurrent load. Broader live testing requires its own
approved fixture matrix, budget, retry limits, and restoration plan.

The audit retains limits on internally consistent historical listings and the
API's 1,000-check-suite boundary; it does not prove atomic history completeness.
Source security assessment does not establish exhaustive security coverage or
validate live authentication and deployment configurations.

## Live evidence and installed callers

The [publication trial evidence](https://github.com/JBallin/claude-review-runtime/pull/7)
supports invocation, completion, and finding publication in a bounded private,
same-personal-owner configuration. It does not measure exhaustive reviewer quality.
Candidate-patch reviews, including the
[ownership review](https://github.com/JBallin/claude-review-runtime/runs/110927696406)
and [audit review](https://github.com/JBallin/claude-review-runtime/runs/110967387622),
verified review completion of those patches; they did not execute the candidates'
ownership behavior or establish adversarial/stress coverage.

The installed OAuth runtime pins are in the
[automatic](../.github/workflows/self-review-automatic.yml),
[manual](../.github/workflows/self-review-manual.yml), and
[status](../.github/workflows/self-review-status.yml) callers.
See the [review guide](review-guide.md) for behavior and update precautions.
Caller adoption does not add live validation; evidence applies only to the
revisions and configurations exercised.

## Remaining boundaries

Confirmed consumer reviews used GitHub.com, hosted Ubuntu runners, and Claude
OAuth with default Anthropic App/OIDC authentication.
[Verified public-consumer evidence](https://github.com/JBallin/claude-review-runtime/issues/29#issuecomment-5974625017)
covers one manual review using the
[installed caller](https://github.com/JBallin/ballin-scripts/blob/729264f35ce52d5605a0b2681b762c85bbe4346e/.github/workflows/claude.yml).
It does not validate automatic triggers or failure paths. Broader public-consumer
coverage, other owners, fork behavior, and GitHub Enterprise Server remain
unvalidated. The automatic entrypoint excludes forks; the manual entrypoint has
limited fork handling, while this repository's caller gate excludes them. These
code paths are not a claim of validated fork support.

Workflow access, runtime-action resolution, and runner preparation can fail
before the trusted helper starts, preventing it from publishing a Check.
App/OIDC validation can instead skip model execution while the Actions workflow
succeeds; the review remains incomplete and cannot establish clean completion.

[Issue 9](https://github.com/JBallin/claude-review-runtime/issues/9) tracks a
specific trusted diagnostic for that provider condition, with the proposed
caller-visible output in
[upstream PR 1879](https://github.com/anthropics/claude-code-action/pull/1879).
A supported upstream revision, reviewed immutable dependency adoption, and tested
runtime Check/status handling are still required. Until then, unknown causes
retain the generic incomplete, non-clean result. Missing execution alone cannot
identify a workflow-validation block, and no merge exception follows.

An observed default-branch validation rejection published a failed Check after
the trusted start job had created it. Inspect the workflow run and captured
runtime identity as well as the Check. Follow the review guide's owner-exception
guidance for initial installation; a skipped model run does not satisfy required
review. Clean completion confirms the runtime's execution and publication
contracts, not the absence of every possible defect. Keep independent review
requirements in place.

## Interpreting results

An accepted `/claude-review` request captures one patch. A completed review with
confirmed inline findings produces an action-required Check and retains those
findings. A clean completed review produces a successful Check and a historical
completion notice; a clean rerun of the same patch identity adds no duplicate
notice. Unconfirmed publication produces an incomplete review and no clean notice.
Prior notices remain historical information after findings or a failed rerun;
consult the current Check and status. Findings remain sticky for the same PR,
head, and merge base through trusted Check evidence.

Offline regressions cover captured-baseline manual reviews, completion signals,
authority checks, and recovery. They do not establish live provider behavior or
integration with the current target baseline. See the
[review contract](review-guide.md#trust-and-review-results) for authoritative behavior.
