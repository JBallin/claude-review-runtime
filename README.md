# Claude Review Runtime

![Claude Review Runtime — AI-powered pull-request reviews](docs/assets/readme-hero.jpg)

Reusable GitHub Actions workflows for Claude pull-request reviews, built on
[Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action).
The runtime captures the patch being reviewed, verifies model completion, and
uses separate trusted jobs to publish the Check and review status. These checks
help distinguish a completed review from a successful workflow that skipped it;
they do not establish exhaustive review quality or merge approval.

[Anthropic’s action](https://github.com/anthropics/claude-code-action) is a
general-purpose integration for reviews and other PR/issue tasks.
[DataDog’s code-review-action](https://github.com/DataDog/code-review-action)
is another reusable review option. Both are credible alternatives to evaluate
against your setup and trust requirements. This runtime focuses on captured-patch
identity, explicit completion checks, and controlled status publication; the
bounded experiments do not establish universal quality or security superiority.

## Set up a consumer

For OAuth on `main`, start with the [consumer guide](docs/consumer-workflows.md): it contains the
prerequisites and three copyable callers for automatic review, manual requests,
and stale-status updates. Pin all three to the same reviewed full runtime SHA.
Model authentication on `main` requires `CLAUDE_CODE_OAUTH_TOKEN`; it does not
accept `ANTHROPIC_API_KEY`. GitHub authentication separately uses the Anthropic
Claude GitHub App and default OIDC exchange.

Choose the [experimental API-key-only branch](https://github.com/JBallin/claude-review-runtime/tree/api-key)
if your consumer requires `ANTHROPIC_API_KEY` for model authentication. It has
not been live-API-tested and does not establish validated API-key support.
Use the variant's [consumer guide](https://github.com/JBallin/claude-review-runtime/blob/api-key/docs/consumer-workflows.md)
for its `ANTHROPIC_API_KEY` caller examples.
`main` remains OAuth-only; neither branch accepts both authentication modes.
Pin all three consumer workflows to one reviewed full commit SHA from the
chosen branch. See the [authentication decision](https://github.com/JBallin/claude-review-runtime/issues/3)
and [variant maintenance guidance](CONTRIBUTING.md#api-key-variant-maintenance).

Used by [Ballin](https://github.com/JBallin/ballin-scripts). See its
[workflow configuration](https://github.com/JBallin/ballin-scripts/tree/main/.github/workflows)
and [pull requests](https://github.com/JBallin/ballin-scripts/pulls) for examples in action.

## Read the result

Use the exact-head `Claude Review` Check together with the workflow run. Inline
comments contain findings; the status comment and top-level PR reactions show
progress and may become stale after a patch change. Request a fresh manual review
when the diff changes. The [review guide](docs/review-guide.md) explains commands,
clean-completion notices, failures, trust boundaries, and rollback.

## Validation and status

This is an experimental runtime with no commitment to ongoing support.
The [validation summary](docs/validation.md) distinguishes tested revisions from
the deployed callers and remaining gaps.
The pinned callers include confirmed finding publication and sanitized Read
coverage diagnostics. A verified Check establishes those runtime contracts; keep
independent review requirements in place.

The current source includes 319 passing offline tests, including bounded
restoration and recovery regressions. The validation summary records the exact
tested revision and the remaining platform and concurrency limits. Source
availability does not establish broad public-consumer or other-owner support.
Consumers retain their own pins until a separate reviewed adoption updates them.
