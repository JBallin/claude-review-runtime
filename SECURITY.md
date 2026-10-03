# 🔒 Security policy

## Reporting

[**Report a vulnerability privately**](https://github.com/JBallin/claude-review-runtime/security/advisories/new)

Do not post exploit details, credentials, or private project content in public
issues or PRs. Include the affected runtime SHA and component, expected security
boundary, impact, and a sanitized reproduction.

## Supported versions

This runtime is experimental, with no commitment to ongoing support or response
times for any revision.

## Runtime security boundaries

PR code, comments, and model output are untrusted. Bind reviews to their captured
PR/head/base/merge-base and runtime identity; incomplete or ambiguous execution
or finding-publication evidence must remain non-clean. Only trusted jobs may
publish Checks and status. Superseded attempts must not replace another request's
current presentation; preserve historical findings and unrelated comments/reactions.

These are intended contracts, not guarantees. See the [review guide](docs/review-guide.md)
for authentication and publication boundaries.
