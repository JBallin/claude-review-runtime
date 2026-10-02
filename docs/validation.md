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
`da81f4b17c0142fec4b6996e6c03aa725394cb1a`. Their manual gate accepts the exact,
case-insensitive `/claude-review` command from trusted PR commenters. This pin
includes the merged finding-publication contract and sanitized captured-read
coverage diagnostics from [PR 16](https://github.com/JBallin/claude-review-runtime/pull/16).
All three pins, manual eligibility, caller tests, and invocation guidance are
updated together; permissions and credentials are unchanged.

Diagnostics run without an opt-in flag when verification reports `verified` or
`captured_inputs_not_read`; earlier failures may have no counters. They report
only fixed metadata/diff labels, counts, content-shape enums, and partial-view
booleans.
They do not export contents, paths, transcripts, or raw errors, and do not alter
the strict completion guard. Offline tests validate this contract; they do not
establish a live diagnostic outcome for PR 11. Its two historical incomplete
Checks remain evidence and are not superseded by a caller update.

[PR 11](https://github.com/JBallin/claude-review-runtime/pull/11) proposes a
manual trigger-comment reaction lifecycle. It remains subject to review gates
and does not update deployed pins or eligibility. That behavior is not part of
the merged publication contract described here.

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
