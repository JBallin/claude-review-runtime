# Claude Review Runtime

Reusable GitHub Actions workflows for automatic Claude pull-request reviews,
trusted manual review requests, and stale-review presentation updates.

This is a private prototype. Cross-repository workflow resolution, default
Anthropic App/OIDC authentication, model execution, and publication have not yet
been validated together in a live consumer. A successful workflow that skips
model execution does not establish that the reviewer works.

## Set up a consumer

1. Use a private consumer repository owned by the same personal account as this
   runtime. The runtime must allow Actions access from that account's private
   repositories (`access_level: user`). This setting grants access across the
   owner's private repositories; it is not a per-consumer allowlist.
2. Install/configure the Anthropic Claude GitHub App for the consumer and store
   the existing Claude OAuth credential as `CLAUDE_CODE_OAUTH_TOKEN` in the
   consumer's Actions secrets. The runtime uses the action's default App/OIDC
   credential path; it does not accept a PAT, custom App credential, or GitHub
   token override.
3. Add the [three caller workflows](docs/consumer-workflows.md), pinning every
   entrypoint to the same reviewed full runtime commit SHA. Forward only the
   named OAuth secret to the two review entrypoints.
4. Keep caller event restrictions and review/merge policy in the consumer. The
   runtime owns its shared review queue; do not add a matching concurrency lock
   to the callers.

The supported platform is GitHub.com with GitHub-hosted Ubuntu runners. The
runtime-local helper action uses `$/actions/review-helper`, which requires runner
version 2.336.0 or later. GitHub Enterprise Server and public consumers are not
validated targets.

## Trust and review results

The Claude job receives repository and pull-request read permissions plus OIDC.
Trusted jobs create and finalize Checks and update status comments/reactions with
publication permissions. Claude's built-in tools are limited to Read, Glob, and
Grep; shell execution and file-editing tools are not enabled. Its inline-finding
tool is backed by the Anthropic App credential; the job's restricted
`GITHUB_TOKEN` permissions do not eliminate the App credential's own authority.

The helper action resolves from the reusable workflow's running commit, without
checking out private runtime code using a separate credential. Captured consumer
head/base identity binds the review to its patch; `job.workflow_repository`,
`job.workflow_sha`, and `job.workflow_file_path` identify the runtime executing
it. These are different provenance boundaries.

The exact-head Check is the review result. The stable comment and owned reactions
are informational presentation and can become stale when the patch changes.
Consumers must reserve `github-actions[bot]` eyes/thumbs-up reactions on top-level
PRs for this runtime: reconciliation identifies the bot/reaction pair, not the
individual workflow that added it.
Completion requires successful unsliced Read calls for both captured input
paths and rejects errored inline-finding tool responses, even if the model
reports success. Missing completion evidence or incomplete execution fails the
review. Findings remain sticky for the same PR, head, and merge base through
trusted Check evidence; findings attributed only to another diff do not carry
across a base retarget. Preexisting findings with unknown diff attribution fail
closed. Raw model transcripts are not uploaded as artifacts. Dependency actions are pinned, but this does not make
their transitive runtime dependencies immutable.

Automatic review excludes drafts, fork pull requests, and Dependabot-triggered runs.
Manual fork requests use top-level comments, with best-effort Check publication
limits. Fork behavior remains unvalidated in this prototype. The status
entrypoint refreshes existing presentation after head changes or base-ref
retargeting; it does not initiate a model review or watch base-tip advancement
on the same branch. Consumers must assess whether a changed base invalidates
previous review evidence.

## Update or roll back

Review and merge a runtime revision before changing consumer pins. Update all
three caller references together to the same full SHA. To roll back, restore all
three references to the prior reviewed SHA. Runtime publication does not update
consumers automatically.

For an isolated trial, restrict caller eligibility to dedicated fixture branches
and prevent another reviewer from running on those fixtures. Restore the
consumer's original workflow configuration through a reviewed pull request when
the trial ends. Keep fixtures and their evidence available for review.

Run the offline regression suite with:

```sh
python3 -m unittest discover -s tests
```

Offline tests establish local contracts; they do not prove private sharing or
cross-repository authentication. No public-release license has been selected.
