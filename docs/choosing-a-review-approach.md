# Choosing a Claude code-review approach

*Sources checked October 31, 2026.*

Claude Review Runtime helps individuals and teams track which changes Claude
reviewed and whether the review completed. Choose it when that evidence is
useful enough to justify operating a GitHub review workflow.

## Quick choice

| Approach | A good fit when you want | What you operate |
| --- | --- | --- |
| [Anthropic's managed Code Review](https://code.claude.com/docs/en/code-review) | Hosted PR reviews with organization controls | App access, review settings, instructions and budget |
| [Official Claude Code Action directly](https://code.claude.com/docs/en/github-actions) | Your own prompts, tools, triggers or broader automation | Workflow configuration, authentication, dependencies and result handling |
| [Claude Review Runtime](consumer-workflows.md#first-review-checklist) | Packaged captured-patch verification and review history | Three pinned caller workflows, credentials, upgrades and recovery |

Managed Code Review runs on Anthropic infrastructure for eligible Team and
Enterprise organizations, without repository workflow files. An Owner enables
it with Claude GitHub App access. You can choose review depth and frequency,
customize review instructions and set a spending limit; the service chooses
models. Checks and finding retention are not unique to Runtime. See the
[setup guide](https://code.claude.com/docs/en/code-review-setup) and
[review customization](https://code.claude.com/docs/en/code-review-customize).

The official Action runs Claude on your GitHub Actions runner. Use it directly
for custom prompts, models, tools or triggers, or broader implementation
automation. Its [authentication options](https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md)
include API keys, subscription OAuth, workload identity federation and custom
GitHub Apps; cloud-provider routes are also available. Those options are not
automatically Runtime capabilities. [Datadog's code review action](https://github.com/DataDog/code-review-action)
is another reusable workflow if multiple model providers are a priority.

## Runtime's contract and maintenance

Runtime builds on the official Action and packages these review safeguards:

- **Captured identity:** records the head, base, merge base and executing Runtime
  revision; the checkout and inline findings target the captured head.
- **Verified completion:** requires foreground completion, successful reads of
  every captured metadata and diff line, and confirmed publication of every
  attempted inline finding. Missing or ambiguous evidence cannot establish a
  clean result.
- **Retained history:** preserves findings for the same PR, head and merge base
  across reruns and recovery. The exact-head `Claude Review` Check is
  authoritative; comments and reactions show progress and history. Status
  refreshes explain stale coverage without reviewing new changes or transferring
  an old Check to a new head.

Runtime verifies that the review completed on the captured changes; it doesn’t
guarantee every bug was found. See the
[review contract](review-guide.md#trust-and-review-results).

Install three separate caller workflows for automatic review, manual review
and stale-status refresh, pinning all three to one reviewed, immutable
40-character Runtime commit. You also need Claude GitHub App access and a model
credential stored as an Actions secret. `main` uses subscription OAuth; API keys
require the separate `api-key` branch, which has not been live-API-tested. Follow
the [setup checklist](consumer-workflows.md#first-review-checklist) for compatible
pins, permissions and validation.

Someone must maintain credentials, App access and coherent caller pins, review
upgrades, and investigate incomplete runs.

Runtime is GitHub-specific, supporting GitHub.com with GitHub-hosted Ubuntu
runners. See [validation limits](validation.md)
for unvalidated configurations. Choose managed review if you do not want to own
workflows, or the Action directly for options Runtime does not expose. Check
[data handling](https://code.claude.com/docs/en/data-usage) against your needs;
runner location alone does not establish privacy or compliance suitability.

Runtime is open source, and issues and contributions are welcome. You can
inspect and adapt it under the [MIT License](../LICENSE), read the
[contribution guide](../CONTRIBUTING.md), or [report an issue](https://github.com/JBallin/claude-review-runtime/issues).

## Evaluate cost

Managed Code Review bills tokens through organization usage credits, separately
from included plan usage, with no flat per-review price. Its multi-agent
operation, review depth and frequency affect spend; see
[managed costs](https://code.claude.com/docs/en/code-review-costs).

Runtime and the Action directly use [subscription usage for OAuth or API billing
for API keys](https://code.claude.com/docs/en/github-actions#manage-costs), plus
[applicable runner and storage costs](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
and maintenance time. Shared API billing does not imply equal costs: no
comparative benchmark establishes Runtime as cheaper or more predictable.
The review job’s 20-minute timeout is not a spending cap.

## Next steps

If Runtime fits, follow the [setup checklist](consumer-workflows.md#first-review-checklist)
and evaluate a few representative PRs for useful findings, incomplete runs,
usage and maintenance effort. Preview the review output in the
[synthetic gallery](review-presentation-gallery.md).
