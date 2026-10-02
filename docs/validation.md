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

This repository’s self-review callers remain pinned to
`9a9310f9ab8374f5c67bd9c0f067b9ff3dac3ebf`. Their manual gate accepts trusted
`@claude` mentions, not `/claude-review`. They have the known
[finding-publication gap](https://github.com/JBallin/claude-review-runtime/issues/6):
a successful Check can coexist with an attempted finding that did not reach GitHub.
Updating pins alone will not adopt the new command. Update all three pins, the
manual gate, caller-contract tests, and invocation guidance in one reviewed change.
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
