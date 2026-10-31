# Design notes

## Review model and effort selection

Explicit model and effort choices can reflect a repository owner's cost or review
preferences. Substituting another setting after an invalid value could spend
quota on a review the owner did not request. Runtime therefore rejects invalid
explicit settings before Claude starts. Defaults apply only to omitted values.

All nonblank workflow inputs must be supported, including an input overridden by
a valid manual choice. This keeps a one-off override from concealing a broken
workflow configuration. After validation, each field resolves from the manual
choice, then the workflow input, then the built-in Sonnet / Medium defaults.
Invalid manual arguments reject the whole request rather than salvaging part of
it. Case and surrounding whitespace are normalized; blank workflow inputs are
omitted.

Workflow configuration errors appear as failed Actions steps with annotations
and a run summary. Invalid manual requests use the separate usage-response path
for eligible commenters. Neither rejection starts a model review, creates a
Claude Review Check, or replaces a completed result. Existing authorization,
immutable capture and receipt ownership still apply.

Post-run model evidence has a different role. When completion is independently
verified, missing or contradictory primary-model evidence remains a model warning
rather than an invalid-input rejection. Runtime does not rewrite the captured
settings or apply new defaults to historical reviews. Effort records the
configured request, not an independently measured execution property.
