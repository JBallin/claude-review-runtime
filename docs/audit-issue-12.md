# Integrated runtime audit

This audit addresses [issue 12](https://github.com/JBallin/claude-review-runtime/issues/12).
It reviews the integrated source and deterministic offline contracts. It does
not establish live model behavior, deployment readiness, or merge approval.

## Frozen revisions

| Role | Full revision |
| --- | --- |
| Audit input and fix branch base | `bf210316642eb37ec5bd040b70cc87275c0a64d3` |
| Input's main parent | `9a20dcdab1081abce44ab4358996016bd3330cdc` |
| Publication integrity, PR 7 | `91f5b76f00800e05ac41f408b427dd60c639d906` |
| Documentation, PR 15 / issue 4 | `65754feb058ea2e196500f1260b6a661468b63d5` |
| Diagnostics, PR 16 | `da81f4b17c0142fec4b6996e6c03aa725394cb1a` |
| Caller adoption, PR 17 | `9a20dcdab1081abce44ab4358996016bd3330cdc` |
| Presentation ownership, PR 11 | `bf210316642eb37ec5bd040b70cc87275c0a64d3` |
| Actual deployed automatic, manual, and status caller pins | `da81f4b17c0142fec4b6996e6c03aa725394cb1a` |

GitHub main and dependency merge revisions were read back before editing and
publication. All listed dependencies are merged. Issue 4 is closed; no unrelated
backlog was treated as a dependency. The final output is the immutable commit
containing this report and the focused fix, recorded in the delivery handoff.
Runtime source publication does not update consumers. Ownership caller adoption
remains a separate delivery; this audit changes no caller pins.

## Finding and disposition

**Check history did not consistently fail closed on missing or malformed API
evidence.** `check_run_history()` accepted successful empty output and `{}` as
an empty history. A completed review with no currently visible comments could
therefore publish `success` without establishing whether prior exact-diff
findings had been recorded and subsequently deleted. Malformed JSON or a null
`check_runs` collection instead escaped error handling and prevented the
finalizer from publishing a failure result.

The focused fix validates every history page and the run fields consumed by
history classification and external-ID recovery. Missing collections, invalid
run shapes and invalid JSON become `GhError`. Existing bounded retries then
lead to incomplete evidence and a failure Check. Independently observed findings
remain recorded. An ambiguous creation POST cannot be retried based on invalid
history. Valid empty collections, extra API fields, multiple pages and nullable
optional output fields remain supported. There is no change to permissions,
model tools, credentials, caller eligibility or publication authority.

The first published correction was too strict about application metadata.
Claude's review of `2d094f61006c7b914f9d0b51d6b8b668bcc42d7a` identified that
GitHub's [official OpenAPI schema](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json)
declares `check-run.app` nullable and the integration's `slug` optional. The
classifier already skips runs without the trusted `github-actions` slug.
The follow-up accepts missing/null app and slug while rejecting wrong non-null
types. It also checks the consumed conclusion field's non-null type. Missing/null
app metadata never grants trusted finding attribution. Regressions cover clean
classification alongside such runs, preserved sticky findings from a separate
trusted run, and rejection of invalid app/slug/conclusion types.

## Reproducible offline evidence

Run from the repository root:

```sh
python3 -m unittest discover -s tests -p test_claude_review_check.py
python3 -m unittest discover -s tests
```

The existing stub GitHub CLI supplies deterministic API responses and records
requests; these tests perform no network operations. New regression cases are
`FinalizeTests.test_invalid_history_fails_closed_and_preserves_observed_findings`
and `CreateAndSnapshotTests.test_ambiguous_create_never_reposts_after_invalid_history`.
The compatibility follow-up adds
`FinalizeTests.test_nullable_untrusted_apps_do_not_hide_trusted_sticky_findings`.

| Fault sequence | Before fix | Expected and observed after fix |
| --- | --- | --- |
| Successful history response is empty or `{}`; completed review; no comments | Success Check | Three bounded history attempts; failure Check; no clean notice |
| Invalid JSON or null collection; otherwise completed review | Unhandled exception; no final PATCH | Failure Check with incomplete-history evidence |
| Invalid later page or malformed run; one newly observed finding | Invalid history could escape classification | Failure Check; finding ID and same-diff presence retained |
| Creation POST fails ambiguously; invalid history lookup | Empty history could permit another POST; malformed history could crash | One POST only; at most two recovery reads; nonzero exit |

The candidate suite contains 291 tests. Independent review reproduced the
original defect and reviewed the focused correction. Exact output revision,
local validation result, and remote CI state are recorded in the handoff; pending
or unavailable external gates must not be represented as passing.

## Coverage and remaining boundaries

- **Eligibility and identity:** traced automatic and manual gates, shared PR
  queue, caller preflight, captured head/base/merge-base, runtime provenance,
  snapshot transfer, and checkout of the captured head. Caller tests exercise
  command, association, draft, closed and fork admission boundaries.
- **Trust and completion:** traced environment/JSON handling of PR text, bundled
  trusted helper resolution, separated model and publisher permissions, allowed
  tools, foreground completion, captured Read coverage, and strict receipt
  cardinality/integer bounds. Existing tests cover missing, malformed, buffered,
  partial, wrong-head and unconfirmed publication.
- **Authority and recovery:** traced Check creation reconciliation, historical
  exact-diff attribution, deleted-finding stickiness, sampled evidence bounds,
  failed/cancelled execution, fallback failure publication and clean-notice
  deduplication. Retries retain three attempts and existing job-timeout budgets.
- **Presentation:** traced acquisition and generation ownership, partial reruns,
  supersession, current-patch reads, same-ref base movement, trigger endpoints,
  stale refresh, unrelated-reaction preservation, ambiguous reaction writes and
  emergency repair. The 120-second presentation budget is independent of Check
  publication; presentation failure cannot strengthen its conclusion.

Beyond the history defect and its application-metadata compatibility correction,
no additional substantive defect was established by this bounded audit.
Duplicate Read responses in a synthetic malformed transcript are a robustness
question: Read coverage currently combines successful returned lines, whereas
inline publication requires exactly one receipt. No practical producer or
attacker-controlled path was established; this is not reported as a vulnerability.
A future offline stress pass may investigate it without relaxing completion.

Existing limitations remain: GitHub provides no atomic compare-and-write for
presentation; concurrency and immediate ownership/patch readbacks reduce races
but do not make them impossible. A lost creation response followed by an
incomplete listing can leave a duplicate in-progress Check. Exhausted API retries
can prevent any final Check update. Status events do not observe every base-tip
movement. Failures before trusted tooling starts may leave no Check. These are
not clean-success evidence or authorization to delete historical state.

Historical failed Checks and prior bounded live evidence remain intact and tied
to their original revisions. No live model run, fixture creation, deployment,
visibility, credential, settings, release, or merge operation is part of this
work. Issue 13's bounded stress work must use the final reviewed candidate SHA
from the handoff and obtain its own applicable authorization.
