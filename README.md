# Claude Review Runtime

*Reusable GitHub Actions workflows for precise, actionable Claude pull-request reviews*

![Claude Review Runtime — AI-powered pull-request reviews](docs/assets/readme-hero.jpg)

Built on [Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action),
this project packages a focused Claude review workflow for your repositories.
Reviews concentrate on significant bugs and correctness issues introduced by the
change, with precision and actionability as design goals. Reusable workflows
provide a consistent way to request reviews and follow their progress across
repositories without maintaining separate review logic in each project.

## Getting Started

The [consumer setup guide](docs/consumer-workflows.md#first-review-checklist)
covers installation with a Claude account token or Anthropic API key and Claude
GitHub App access to your repository. The runtime targets GitHub.com with
GitHub-hosted Ubuntu runners; the guide explains the prerequisites and validation
limits for your chosen authentication mode.

Once installed, reviews run automatically when an eligible non-draft PR is
opened or marked ready for review. To review an existing PR or request another
pass after changes, post `/claude-review` as a PR comment. Automatic reviews
exclude fork PRs and Dependabot-triggered runs; manual requests require a trusted
commenter.

Findings appear inline on the reviewed file and line. A shared review-status
comment shows progress and results for the latest accepted attempt. The
[review guide](docs/review-guide.md) explains how to interpret results, check
coverage after changes, and recover incomplete reviews.

Used by [Ballin](https://github.com/JBallin/ballin-scripts). Its
[workflow configuration](https://github.com/JBallin/ballin-scripts/tree/main/.github/workflows)
and [pull requests](https://github.com/JBallin/ballin-scripts/pulls) show the setup
and review experience in practice.

## Background and Alternatives

[Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action)
can also be used directly for reviews and other PR/issue tasks.
[DataDog’s code-review-action](https://github.com/DataDog/code-review-action)
is another reusable review option.

The review format and flow were inspired by
[OpenAI’s Codex code review](https://learn.chatgpt.com/docs/third-party/github).
