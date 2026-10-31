# Design notes

## Execution limits

Automatic and manual reviews omit an explicit turn cap and retain a 20-minute
review-job timeout. This favors letting a review finish over rejecting a successful
SDK result because of a count whose relationship to the native budget is uncertain.
It gives up a separate fast-loop backstop; the elapsed-time boundary remains.
Neither control is an exact token, cost, or quota budget.

### Three different controls

- **SDK native cap:** the pinned Action [maps `--max-turns` to SDK
  `maxTurns`](https://github.com/anthropics/claude-code-action/blob/5eb090f3c3afd701a0672cd32981197ca27688f6/base-action/src/parse-sdk-options.ts#L316-L323).
  Native exhaustion can return `error_max_turns`. Omission leaves this option
  undefined; it does not configure a zero-turn budget.
- **Action post-success comparison:** the pinned Action [rejects SDK success
  when reported `num_turns` exceeds `maxTurns`](https://github.com/anthropics/claude-code-action/blob/5eb090f3c3afd701a0672cd32981197ca27688f6/base-action/src/run-claude-sdk.ts#L242-L250).
  This happens after execution and cannot recover usage already incurred.
- **GitHub job timeout:** the review job's 20 minutes cover checkout, setup,
  model execution, and verification together. GitHub [defaults to 360 minutes
  if the job timeout is omitted](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idtimeout-minutes).
  The [pinned SDK execution path](https://github.com/anthropics/claude-code-action/blob/5eb090f3c3afd701a0672cd32981197ca27688f6/base-action/src/run-claude-sdk.ts#L189-L214)
  relies on the workflow timeout rather than imposing its own overall timer.

### Evidence and alternatives

Upstream [PR #1607](https://github.com/anthropics/claude-code-action/pull/1607)
added the defensive post-success check after a configured cap of 60 returned
success at 73 reported turns. Open [PR #1762](https://github.com/anthropics/claude-code-action/pull/1762)
proposes removing that comparison while retaining native enforcement.
[Issue #1795](https://github.com/anthropics/claude-code-action/issues/1795)
reports a mismatch between API-round limits and counts that include tool results.
That is reporter evidence, not independent proof of our pinned SDK's semantics.
In particular, comparing `num_turns` with `maxTurns` does not by itself prove
native budget exhaustion.

Runtime's public observations at configured cap 30 include
[success at 31](https://github.com/JBallin/claude-review-runtime/actions/runs/41128856237/job/124314559697),
[native error at 31](https://github.com/JBallin/claude-review-runtime/actions/runs/41152435649/job/124391903273),
and [success at 33](https://github.com/JBallin/claude-review-runtime/actions/runs/41153547174/job/124395408056).
The Action rejected both successes afterward. These three runs used SDK 0.3.303
and CLI 2.1.303, but different patches and models prevent a controlled comparison.
They establish neither actual API-round counts nor a universal offset.
Earlier [PR #44](https://github.com/JBallin/claude-review-runtime/pull/44)
raised the manual cap from 15 to 30 for review headroom while retaining 20 minutes;
earlier runs also involved different versions.

Official examples are task-dependent: [configuration illustrates 5](https://github.com/anthropics/claude-code-action/blob/5eb090f3c3afd701a0672cd32981197ca27688f6/docs/configuration.md#L203-L223),
[usage illustrates optional 10](https://github.com/anthropics/claude-code-action/blob/5eb090f3c3afd701a0672cd32981197ca27688f6/docs/usage.md#L25-L35),
and the [comprehensive review example](https://github.com/anthropics/claude-code-action/blob/5eb090f3c3afd701a0672cd32981197ca27688f6/examples/pr-review-comprehensive.yml)
omits a cap. These do not establish a universal standard for review workloads.

Raising the cap would retain a fast-loop backstop but still depend on uncertain
counter relationships. Patching or replacing the Action would add maintenance and
dependency review. Omitting the cap avoids its post-success rejection without
changing the pinned Action or weakening Runtime's independent completion checks.
The earlier 100-turn proposal is superseded.

### Visibility and when to reconsider

The existing collapsed PR Details and Actions run summary show **Turns**, using
the same validated, attempt-bound SDK `num_turns` receipt. Missing or unreliable
data is unavailable. This count is not a measurement of API rounds, tokens, cost,
quota, or the native turn budget. A count or SDK success does not prove that the
captured review completed. Historical configured-cap diagnostics remain supported.

Revisit the choice if retained evidence shows harmful rapid looping within the
timeout, repeatable timeout failures caused by legitimate review work, or upstream
changes establish reliable native-budget semantics and remove the problematic
comparison. Evaluate the exact Action/SDK versions and review workload then.
No consumer limit settings or automatic full-review retries are added without a
demonstrated need.
