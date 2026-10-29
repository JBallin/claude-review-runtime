# What is Claude Review Runtime?

Claude Review Runtime helps people keep track of automated reviews of proposed
code changes. It makes review progress and results easier to understand, so the
person deciding what to accept has clearer information and less to track by hand.

## The problem

Reviewing a proposed change can uncover problems before it is accepted. But a
review takes time, and the proposal can change along the way. A result for an
earlier version doesn't tell someone whether the revised change was reviewed.

## What it does

Claude, an AI assistant, examines the proposed change and can flag potential
problems. Runtime coordinates the review, shows whether it is running,
completed, or failed, and identifies the version the review was for. It helps
people see when an earlier result may no longer apply and a new review may be
needed.

A completed review with no reported problems does not guarantee bug-free
software or replace the judgment of the person deciding what to accept.

## See it in action

The [review presentation gallery](review-presentation-gallery.md) shows
illustrative, made-up examples of review statuses and results.
