# What is Claude Review Runtime?

Claude Review Runtime helps people review proposed code changes before deciding
whether to accept them. Claude, an AI assistant, examines changes and can flag
potential problems. Runtime coordinates those reviews and makes it easier to see
what needs attention, whether a review finished, and which version it covered.
That means less manual tracking for the person making the decision.

If a proposed fix changes after review, the earlier result covers the old version,
not the revised one. Runtime helps make that distinction clear, so people can
see when a review may no longer apply.

A completed review with no reported problems does not guarantee bug-free
software or replace human judgment.

The [review presentation gallery](review-presentation-gallery.md) shows
illustrative, made-up examples of review statuses and results.
