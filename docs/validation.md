# Validation and limitations

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

The tested configuration uses GitHub.com, hosted Ubuntu runners, an existing
Claude OAuth secret, and the Anthropic App/default OIDC path. Public consumers,
other owners, fork behavior, and GitHub Enterprise Server remain unvalidated.
The automatic entrypoint excludes forks; the manual entrypoint has limited fork
handling, while this repository’s caller gate excludes them. These code paths
are not a claim of validated fork support.

Workflow access, runtime-action resolution, and runner preparation can fail
before the trusted helper starts, preventing it from publishing a Check.
App/OIDC validation can instead skip model execution while the Actions workflow
succeeds; the review remains incomplete and cannot establish clean completion.
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
