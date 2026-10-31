# Choosing a Claude code-review approach

*Official sources checked October 31, 2026. Runtime behavior describes this
revision; consumers run the revision pinned in their workflows.*

Claude Review Runtime is for engineers and teams that want a reusable GitHub
review workflow with an explicit record of what was reviewed and whether the
review completed. Choose it when that contract justifies maintaining the
workflows.

## Quick choice

| Approach | A good fit when you want | What you operate |
| --- | --- | --- |
| Anthropic's managed Code Review | Hosted PR reviews with organization controls | App access, review settings, instructions and budget |
| Official Claude Code Action directly | Your own prompts, tools, triggers or broader automation | Workflow configuration, authentication, dependencies and result handling |
| Claude Review Runtime | Packaged captured-patch verification and review history | Three pinned caller workflows, credentials, upgrades and recovery |

[Managed Code Review](https://code.claude.com/docs/en/code-review) is available
to eligible Team and Enterprise organizations. Reviews run on Anthropic
infrastructure using multiple agents and finding verification, without repository
workflow files. An organization Owner enables it with Claude GitHub App access.
Owners choose review depth and frequency; the service chooses models, with no
admin model selector. Repository instructions customize what it flags. See the
[setup guide](https://code.claude.com/docs/en/code-review-setup) and
[review customization](https://code.claude.com/docs/en/code-review-customize).

Managed review already posts inline findings and a Check, retains open findings,
and can reuse earlier results when the effective diff is unchanged under its
documented conditions. Its own Check never fails, but its machine-readable
severity counts and documented missing-result handling let you build a separate
CI gate. Checks and finding retention are not exclusive Runtime capabilities.
See [managed results and gating](https://code.claude.com/docs/en/code-review-pull-requests).

The [official Action](https://code.claude.com/docs/en/github-actions) runs Claude
on your GitHub Actions runner. Use it directly when you need control over the
prompt, model, tools or events, or want implementation automation alongside
reviews. It supports API keys, subscription OAuth and cloud-provider routes.
Upstream also documents [Workload Identity Federation and custom GitHub Apps](https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md).
Those upstream options are not automatically Runtime capabilities.

For occasional reviews, [Claude Code's local `/code-review` command](https://code.claude.com/docs/en/code-review-local)
may be enough. A local session still uses remote model inference; it does not
mean offline operation. [Datadog's code review action](https://github.com/DataDog/code-review-action)
is another reusable workflow to consider if multiple model providers are a
priority.

## Runtime's contract and maintenance

Runtime builds on the official Action and packages a particular review contract:

- **Captured identity:** the review records its head, base, merge base and
  executing Runtime revision. The checkout and inline-finding targets use the
  captured head rather than silently following later PR changes.
- **Verified execution:** a clean result requires verified foreground completion,
  successful reads of every captured metadata and diff line, and confirmed
  publication of each attempted inline finding. Missing or ambiguous evidence
  cannot establish clean completion.
- **Retained history:** recorded findings remain findings for the same PR, head
  and merge base across reruns and recovery. The exact-head `Claude Review`
  Check is authoritative; comments and reactions present its progress and
  history. Status refreshes explain stale coverage without reviewing new code
  or transferring an old Check to a new head.

These checks establish execution and evidence properties, not exhaustive review
coverage or superior bug detection. A clean review is not merge approval.
See the [review contract](review-guide.md#trust-and-review-results).

Installation means adding **three separate caller workflows**: automatic review,
manual review and stale-status refresh. Pin all three to one reviewed, immutable
40-character Runtime commit from the appropriate authentication branch. Install
the Claude GitHub App and save the model credential as an Actions secret; the
status caller needs no model secret. Review and merge the caller installation,
then validate it on a separate eligible PR. The
[consumer setup guide](consumer-workflows.md#first-review-checklist) owns the
workflow examples and permissions.

`main` requires `CLAUDE_CODE_OAUTH_TOKEN`, obtained through an eligible Claude
subscription. API-key authentication uses the separate `api-key` branch and
`ANTHROPIC_API_KEY`; that path has not been live-API-tested. Runtime does not
support custom GitHub authentication or expose upstream federation/provider
configuration.
OAuth uses the token creator's subscription. For a secret shared across
repositories, [upstream recommends an API key](https://code.claude.com/docs/en/github-actions#set-up-for-an-organization)
rather than OAuth. Runtime's API-key path still requires its separate branch
and remains unvalidated by live API testing; upstream's recommendation does not
establish validated Runtime support.

Someone must maintain credential validity, App access and coherent caller pins,
review upgrades, and investigate incomplete runs. A green Actions job alone does
not prove review completion. New patches need fresh review when required;
status-only refresh never runs Claude. Configure caller events deliberately,
including target-branch push refreshes where needed.

Choose another approach if you want managed operation or need unsupported
authentication or deployment options. Runtime's supported platform is GitHub.com
with GitHub-hosted Ubuntu runners. Other-owner deployments, forks and GitHub
Enterprise Server remain unvalidated; see [validation limits](validation.md).
Neither runner location nor Runtime's verification contract establishes privacy
or compliance superiority. Check each option's [data handling](https://code.claude.com/docs/en/data-usage) and eligibility
against your requirements.

## Evaluate cost

Managed Code Review has **no flat per-review price**. It bills token usage through
organization usage credits, separately from included plan usage, and offers a
monthly service spend limit. Depth, change complexity and review frequency affect
spend. See [managed costs](https://code.claude.com/docs/en/code-review-costs).

With Runtime or the Action directly, [OAuth runs consume subscription usage;
API-key runs incur API billing](https://code.claude.com/docs/en/github-actions#manage-costs).
Include runner and artifact costs under
[GitHub's billing rules](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
plus maintenance time. Standard hosted runners in public repositories are free;
private-repository usage has allowances and possible additional charges.
Runtime's time and turn limits are not spending caps. There is no measured
cost comparison here establishing that Runtime is cheaper or more predictable.

## Next steps

Start with your account eligibility, required review contract and willingness to
own workflows. If Runtime fits, follow the setup checklist and assess the Check
on the exact reviewed patch. Ballin, a personal macOS project, is one usage
context; Runtime itself runs in GitHub Actions. Evaluate representative PRs for
useful findings, incomplete runs, usage and maintenance effort before expanding
adoption. Keep human review and your merge policy in place with every option.
