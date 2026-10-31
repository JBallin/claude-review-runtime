# Validation and limitations

## Configurations

PR Details and the Actions run summary display **Turns** from the same bounded,
captured-attempt SDK `num_turns` receipt, independently of any configured cap.
Only a strictly validated integer from a unique same-session terminal result is
accepted; missing historical data remains unavailable. Counts do not establish
completion or measure API rounds, tokens, cost, quota, or the native turn budget.
See [Execution limits](design-notes.md#execution-limits) for the design rationale.

Runtime jobs explicitly select `ubuntu-26.04`. This fixes the Ubuntu release;
GitHub can still update the image and bundled tools. CI logs its actual image
and selected tool versions for compatibility diagnosis.

Live consumer reviews have exercised GitHub.com, hosted Ubuntu runners, and
Claude OAuth with default Anthropic App/OIDC authentication, including automatic
and manual reviews. The API-key branch has not been live-tested.
Live finding publication has been exercised in a bounded private,
same-personal-owner configuration. See the [consumer guide](consumer-workflows.md)
for setup and branch selection.

Other owners, GitHub Enterprise Server, and broader public-consumer failure
scenarios remain unvalidated. The automatic entrypoint excludes forks; the
manual entrypoint has limited fork handling, while this repository's callers
exclude them. These code paths do not establish validated fork support.

## Choosing validation

For compatible dependency updates, use required offline checks, independent
review, and verified adoption in a normal review. Propose a separate live
validation run only for material gaps, under the
[existing approval requirements](../AGENTS.md#scope-and-runtime-adoption).
Preserve fail-closed safeguards and state unverified behavior. Changing pins
alone does not establish live authentication, model completion, or publication.

## Coverage limits

Deterministic tests use separate helper processes and persisted synthetic API
state to exercise patch restoration, process restarts, competing writers,
history outages, and lost or partial receipts. They check that missing or
ambiguous evidence stays non-clean, recorded findings remain sticky, and obsolete
writers cannot replace a newer owner's presentation in the tested schedules.

Synthetic recovery tests do not establish behavior under real service outages,
actual runner cancellation or queue delivery, artifact transport, or sustained
concurrent load. Controlled provider interruption is distinct from ordinary
Actions cancellation. Synthetic finding-receipt tests do not execute or interrupt
the finding publisher, and the expired API deadline test does not validate
lifecycle deadline setup or reset.

Separate API reads and writes are not atomic: ownership checks reduce races
without eliminating them. Internally consistent history listings do not prove
completeness, and the Check lookup is limited to the most recent 1,000 check
suites. Source security review and finite test schedules do not establish
exhaustive security coverage or validate live deployment configurations.

## Interpreting a review

Use the authoritative Check and captured review identity to assess the result.
A successful Check confirms verified completion without recorded findings for
that captured patch; it is not exhaustive assurance that no defect exists.
Completion notices are best-effort historical information, not review authority
or merge approval. See the [review contract](review-guide.md#trust-and-review-results)
for finding retention, publication safeguards, and result interpretation.

Workflow access, runtime-action resolution, or runner preparation can fail
before trusted tooling starts, leaving no Check. App/OIDC workflow validation
can also skip model execution while Actions succeeds. Missing execution alone
does not identify the cause, and a skipped or incomplete run does not satisfy
required review. Use the [incomplete-review guidance](review-guide.md#installation-and-incomplete-reviews)
to investigate; keep independent review and merge requirements in place.

## Selection evidence

Offline tests cover selection admission, safe CLI arguments and attempt-bound
model reporting, including primary-session initialization/assistant evidence,
auxiliary usage, conflicting primary sources, legacy records, and standing/historical
presentation. Primary evidence requires one initialization, assistant turns, and
one result in the same session. Nested auxiliary turns and the result's global
usage bag cannot confirm the primary model; source model IDs and execution
attempts remain in diagnostic metadata. Model metadata does not determine review
completion. These tests do not verify account access, live model selection or fallback, or
effective effort.
