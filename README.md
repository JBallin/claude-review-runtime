# Claude Review Runtime

Reusable GitHub Actions workflows for precise, actionable Claude pull-request reviews

![Claude Review Runtime — AI-powered pull-request reviews](docs/assets/readme-hero.jpg)

Built on [Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action),
the runtime focuses reviews on significant bugs introduced by the diff and posts
findings inline, tied to the reviewed file and line.

## Getting Started

Follow the [consumer setup guide](docs/consumer-workflows.md#first-review-checklist)
to get started with a Claude account token or Anthropic API key.

## Read the Result

Findings appear as inline PR comments. The review-status comment shows progress
and results; see the [review guide](docs/review-guide.md) for interpreting them
and requesting another review.

## Examples

Used by [Ballin](https://github.com/JBallin/ballin-scripts). See its
[workflow configuration](https://github.com/JBallin/ballin-scripts/tree/main/.github/workflows)
and [pull requests](https://github.com/JBallin/ballin-scripts/pulls) for real reviews.

## Background and Alternatives

[Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action)
can also be used directly for reviews and other PR/issue tasks.
[DataDog’s code-review-action](https://github.com/DataDog/code-review-action)
is another reusable review option.

The review format and flow were inspired by
[OpenAI’s Codex code review](https://learn.chatgpt.com/docs/third-party/github).
