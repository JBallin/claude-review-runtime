# Claude Review Runtime

*Reusable GitHub Actions workflows for precise, actionable Claude pull-request reviews*

![Claude Review Runtime — AI-powered pull-request reviews](docs/assets/readme-hero.jpg)

Built on [Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action),
Runtime makes review scope and status inspectable: it records the reviewed head
and base commits and distinguishes current results from stale or incomplete
review evidence.

## Usage

Use a Claude account token or an Anthropic API key for model authentication.
Follow the [consumer setup guide](docs/consumer-workflows.md#first-review-checklist)
to install the workflows for your chosen authentication mode.

Reviews run automatically when an eligible non-draft PR is opened or a draft is
marked ready for review. Authorized users can also post `/claude-review` as a PR
comment to request an on-demand review, including another pass after changes.

Findings appear inline. A standing review-status comment is updated as the
review progresses, with 👀 and 👍 reactions on the PR and accepted manual requests
signaling progress and verified clean completion. A verified-clean manual review
may also post a separate completion notice. The review format and flow were
inspired by [OpenAI’s Codex code review](https://learn.chatgpt.com/docs/third-party/github).
See the [review guide](docs/review-guide.md).

## Examples and Alternatives

For a working example, see Ballin’s
[workflow configuration](https://github.com/JBallin/ballin-scripts/tree/main/.github/workflows)
and [pull-request reviews](https://github.com/JBallin/ballin-scripts/pulls).

You can also use [Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action)
directly or [DataDog’s code-review-action](https://github.com/DataDog/code-review-action).
