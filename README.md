# Claude Review Runtime

![Claude Review Runtime — AI-powered pull-request reviews](docs/assets/readme-hero.jpg)

Reusable GitHub Actions workflows for Claude pull-request reviews, built on
[Anthropic’s Claude Code Action](https://github.com/anthropics/claude-code-action).
The runtime captures the patch, checks that Claude read the full captured input
and completed the review, and uses trusted jobs to publish the result. This helps
distinguish a completed review from a successful workflow that skipped it.
A completed review is not exhaustive assurance or merge approval.

## Set up a consumer

Start with the [consumer setup guide](docs/consumer-workflows.md#first-review-checklist):

1. Choose `main` with `CLAUDE_CODE_OAUTH_TOKEN`, or the experimental
   [`api-key` branch](https://github.com/JBallin/claude-review-runtime/tree/api-key)
   with `ANTHROPIC_API_KEY` (not live-API-tested). Both require Claude GitHub App
   access to the consumer repository.
2. Install the automatic review, manual review, and status workflows, pinned to
   one reviewed immutable commit from the selected branch.
3. Have the installation reviewed and merged, then verify a first approved live
   review on a separate PR that does not change workflows.

The guide includes credentials, permissions, workflow examples, and installation
failure recovery. The exercised platform is GitHub.com with hosted Ubuntu
runners; see [validation and limitations](docs/validation.md) before adoption.

## Read the result

Inspect the `Claude Review` Check on the reviewed head together with the workflow
run. A clean result requires verified completion without recorded findings;
inline comments contain findings to assess. A skipped or incomplete run does not
satisfy review requirements.

The status comment and PR reactions show progress and historical results. They
can become stale after a patch change. Request a fresh manual review when the
diff changes, and assess whether a changed base invalidates previous evidence.
The [review guide](docs/review-guide.md) explains commands, captured-patch
coverage, trust boundaries, and recovery. Keep independent review and merge
requirements in place.

## Background and alternatives

Used by [Ballin](https://github.com/JBallin/ballin-scripts); its
[workflow configuration](https://github.com/JBallin/ballin-scripts/tree/main/.github/workflows)
and [pull requests](https://github.com/JBallin/ballin-scripts/pulls) provide examples.
The review format and flow were inspired by
[OpenAI’s Codex code review](https://learn.chatgpt.com/docs/third-party/github).

[Anthropic’s action](https://github.com/anthropics/claude-code-action) supports
reviews and other PR/issue tasks;
[DataDog’s code-review-action](https://github.com/DataDog/code-review-action)
is another reusable review option. Evaluate them against your setup and trust
requirements. This runtime’s bounded validation does not establish universal
review quality or security superiority.
