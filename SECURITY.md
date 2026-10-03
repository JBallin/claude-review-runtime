# Security policy

## Reporting a vulnerability

GitHub private vulnerability reporting is the planned reporting channel once
this repository is public and the feature has been enabled and verified. It is
not available for private repositories. Choosing this channel does not change
repository visibility or enable the feature.

After reporting is enabled, use **Report a vulnerability** on the repository's
[Security Advisories page](https://github.com/JBallin/claude-review-runtime/security/advisories).
If that button is unavailable, the reporting route is not ready; do not post
exploit details, credentials, or private project content in a public issue or PR.
Maintainers must verify reporting availability after publication before claiming
that reports can be submitted.

A report should identify the reviewed runtime SHA, affected component, expected
security property, realistic impact, and a sanitized reproduction. This is an
experimental runtime with no commitment to ongoing support or response times.

## System and security boundaries

The reusable workflows capture a consumer PR's patch identity and separate the
model review job from trusted Check and status publication. The helper in
`actions/review-helper` verifies execution and finding evidence. Authentication
and publication rely on GitHub Actions, the Anthropic App/OIDC path, the pinned
provider action, and the consumer's OAuth credential.

PR code, comments, and model output are untrusted inputs. Captured
PR/head/base/merge-base and runtime identity must remain bound to the review.
Incomplete or ambiguous execution and publication evidence must remain non-clean.
Trusted publication ownership must prevent superseded attempts from replacing
another request's current presentation. Historical findings and unrelated
comments and reactions must be preserved.

These are the intended contracts, not claims that testing proves every control.
The [review guide](docs/review-guide.md) records authentication and permission
boundaries, exact-head Check authority, and best-effort presentation behavior.
The [validation summary](docs/validation.md) records tested revisions, the bounded
security scan, and remaining platform and concurrency limitations. It does not
establish validated public-consumer, other-owner, fork, or GHES support.
