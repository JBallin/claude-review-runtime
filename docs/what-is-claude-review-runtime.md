# What is Claude Review Runtime?

Developers can add Claude Review Runtime to a GitHub project to have Claude
automatically review proposed code changes. Claude can flag potential problems
before anyone accepts a change. The Claude Code Action already offers code
review; Runtime builds on it to coordinate reviews and make their progress and
results easier to understand.

## Did Claude review this version?

A developer might revise a fix after Claude reviews it. The earlier review is
still useful, but it doesn't cover the revised change. Runtime keeps track of
which version a review was for and helps show when its result may no longer
apply. It also shows whether the review is still running, finished, or failed.

That gives the person deciding what to accept clearer information and less to
track by hand. They can use it to consider Claude's feedback or decide whether
another review is needed.

A completed review with no reported problems doesn't guarantee bug-free
software or replace human judgment.

## See it in action

The [review presentation gallery](review-presentation-gallery.md) shows
illustrative, made-up examples of review statuses and results.
