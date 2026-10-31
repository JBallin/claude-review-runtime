# Claude Review Runtime

*Reusable GitHub Actions for Claude pull-request reviews with status reporting*

![Claude Review Runtime — AI-powered pull-request reviews](docs/assets/readme-hero.jpg)

Built on [Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action),
Claude Review Runtime helps you see which commits Claude reviewed, whether the
review completed, and whether the results are still current.

Read the [plain-language introduction](docs/what-is-claude-review-runtime.md).

## Usage

Supports both Claude account tokens and Anthropic API keys; follow the
[consumer setup guide](docs/consumer-workflows.md#first-review-checklist) to get started.

Reviews run automatically when a PR becomes ready for review. Authorized users
can also comment `/claude-review` on a PR to request an on-demand review.
Set [model and effort defaults](docs/consumer-workflows.md#model-and-effort-defaults)
in your workflow, and [override them per request](docs/consumer-workflows.md#optional-model-and-effort-selection).

A standing status comment is updated as automatic and manual reviews progress.
👀 and 👍 reactions appear on the PR and on manual review request
comments. Manual reviews post a completion comment on success.
Incomplete reviews include an explanation and recovery guidance.
The review format and flow were inspired by
[OpenAI’s Codex code review](https://learn.chatgpt.com/docs/third-party/github).
See the [review guide](docs/review-guide.md) for more details.

## Examples

Browse the [review presentation gallery](docs/review-presentation-gallery.md) for
complete synthetic examples of review statuses and completion comments.

For actual usage, see Ballin’s
[workflow configuration](https://github.com/JBallin/ballin-scripts/tree/main/.github/workflows)
and [pull-request reviews](https://github.com/JBallin/ballin-scripts/pulls).

## Alternatives

- [Claude Code GitHub Action](https://code.claude.com/docs/en/github-actions):
  Use the underlying GitHub Action directly.
- Anthropic’s [managed Code Review](https://code.claude.com/docs/en/code-review):
  Anthropic-hosted reviews for eligible Team and Enterprise plans.
- [Datadog’s code review action](https://github.com/DataDog/code-review-action):
  Another GitHub Actions option.
