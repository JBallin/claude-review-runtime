# Claude Review Runtime

*Reusable GitHub Actions for Claude pull-request reviews with status reporting*

![Claude Review Runtime — AI-powered pull-request reviews](docs/assets/readme-hero.jpg)

Built on [Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action),
Claude Review Runtime helps you see which commits Claude reviewed, whether the
review completed, and whether the results are still current.

## Usage

Supports both Claude account tokens and Anthropic API keys; follow the
[consumer setup guide](docs/consumer-workflows.md#first-review-checklist) to get started.

Reviews run automatically when a PR becomes ready for review. Authorized users
can also comment `/claude-review` on a PR to request an on-demand review.

A standing status comment is updated as automatic and manual reviews progress.
👀 and 👍 reactions appear on the PR and on manual review request
comments. Manual reviews post a completion comment on success.
The review format and flow were inspired by
[OpenAI’s Codex code review](https://learn.chatgpt.com/docs/third-party/github).
See the [review guide](docs/review-guide.md) for more details.

## Examples and Alternatives

For a working example, see Ballin’s
[workflow configuration](https://github.com/JBallin/ballin-scripts/tree/main/.github/workflows)
and [pull-request reviews](https://github.com/JBallin/ballin-scripts/pulls).

Alternatives include using [Claude Code Action](https://github.com/anthropics/claude-code-action)
directly or [Datadog’s code review action](https://github.com/DataDog/code-review-action).
