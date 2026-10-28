# What is Claude Review Runtime?

Claude Review Runtime helps people keep track of automated reviews of proposed
software changes: updates suggested for a software project before they are
accepted. Claude, an AI assistant, examines a change and can point out potential
problems. Runtime coordinates the review and reports its status, so the person
responsible for the software has clearer information when deciding what to
accept.

The project grew out of work to add Claude reviews to Ballin, a tool for
maintaining a Mac. Making the review workflow reusable lets other software
projects use it too.

## Why review a change?

Software is made from code: instructions that tell a computer what to do. A
proposed change might fix a problem or add a feature, but it can also introduce
mistakes or unexpected effects. Reviewing it before accepting it gives someone
a chance to spot concerns and ask for corrections.

An automated review can help with that work. But the review itself is only
part of the story: people also need to know whether it finished and which
version of the proposed change it examined. Runtime makes that tracking and
status easier to understand, reducing the work of keeping track by hand.

## Why does the version matter?

Imagine a shopping-list app sometimes loses saved items. A developer proposes
a fix, and Claude's review points out that the fix might also erase items
already checked off. The developer revises the fix to keep those items. The
earlier review is still useful history, but it does not tell us whether Claude
reviewed the new version. A new review can examine the revised change. Runtime
records the version a review covered and helps show when an earlier result may
no longer apply. It also reports when a review is still running or did not finish.

Even a completed review with no reported problems does not guarantee that the
software is bug-free. The person deciding what to accept still needs to use
their judgment and consider other checks and reviews.

## See it in action

The [review presentation gallery](review-presentation-gallery.md) shows
illustrative, made-up examples of review statuses and results.
