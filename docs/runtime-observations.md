# Runtime observations

This operational record preserves dated observations, not permanent model,
effort or deployment guarantees. Use the [consumer guide](consumer-workflows.md#optional-model-and-effort-selection)
for supported commands and upstream configuration guidance. A caller's eligibility
gate must match its selected Runtime revision; verify the deployed caller before
requesting overrides.

## 2026-10-26: self-review deployment at PR #87 review

The [self-review caller](../.github/workflows/self-review-manual.yml) accepted
only plain `/claude-review` at Runtime PR #87 head `2d9ba4f53ccb5d2a776ea7d66e4a7ff32b5dc183`. The override feature did not
change caller gates or pins. This records deployment state at review time,
not a limitation of the supported Runtime command contract.

## 2026-10-25: observed model and documented effort default

Two successful automatic reviews, Actions runs `40153102321` and `40193826343`,
reported `claude-sonnet-5-5` in their structured init records; result model usage
also included `claude-haiku-5-5`. The latter run used Claude Code 2.1.303 through
Anthropic action `5eb090f3c3afd701a0672cd32981197ca27688f6`.

Static inspection of that CLI's model catalog found Sonnet 5.5's default effort
set to `medium`, consistent with upstream documentation at the time. Neither run
reported resolved effort, so this does not establish that either ran at medium.
The model observations likewise do not establish why configuration selected
Sonnet or guarantee every internal call used it.

The identifiers and findings above remain here independently of Actions log
retention. The [later run](https://github.com/JBallin/claude-review-runtime/actions/runs/40193826343)
is supplementary evidence, not the permanent usage contract.
