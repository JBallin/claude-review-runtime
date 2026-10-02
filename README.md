# Claude Review Runtime

Reusable GitHub Actions workflows for automatic Claude pull-request reviews,
trusted manual review requests, and stale-review presentation updates, built
on Anthropic's Claude Code Action. Reviews use captured PR inputs; separate
trusted jobs publish the Check and review status.

This is a private prototype. A successful workflow that skips model execution
does not establish a completed review.

## Set up a consumer

Use a private repository owned by the same personal account on GitHub.com, with
GitHub-hosted Ubuntu runners, runtime Actions access, the Anthropic Claude GitHub
App, and the existing OAuth secret `CLAUDE_CODE_OAUTH_TOKEN`. Install all three
[caller workflows](docs/consumer-workflows.md),
pinning them to the same reviewed full runtime SHA. For example, the automatic
caller is:

```yaml
name: Automatic Claude review
on:
  pull_request:
    types: [opened, ready_for_review]
permissions: {}
jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
      checks: write
      issues: write
      id-token: write
    uses: JBallin/claude-review-runtime/.github/workflows/claude-review.yml@9a9310f9ab8374f5c67bd9c0f067b9ff3dac3ebf
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

The [consumer guide](docs/consumer-workflows.md) covers prerequisites, permissions,
and all three caller examples. The [review guide](docs/review-guide.md) explains
results, manual invocation, this repository's pinned callers, troubleshooting,
and rollback. Required Claude review remains unsatisfied when bootstrap
validation skips model execution; a green workflow alone cannot satisfy it.
The baseline pin also has a known
[finding-publication gap](https://github.com/JBallin/claude-review-runtime/issues/6);
a green Check alone is insufficient merge evidence.

## Validate locally

```sh
python3 -m unittest discover -s tests
```

Offline tests establish local contracts; they do not prove live authentication
or model execution.

## License

Licensed under the [MIT License](LICENSE).
