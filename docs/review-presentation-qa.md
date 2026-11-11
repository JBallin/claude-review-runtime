# Review presentation QA

Use this process when changing generated review comments or their presentation.
The [synthetic gallery](review-presentation-gallery.md) exercises production
presentation helpers with fixed fixtures. It supports repeatable rendering and
interaction checks without running a model or posting test comments.

## 1. Prepare the candidate and coverage

- Identify the candidate revision and inspect the affected helpers, tests, and
  gallery diff. Cover changed states plus representative unchanged cases. When
  a shared renderer or layout change can affect the whole gallery, inspect every
  section. Include distinct conditional, error, recovery, and fallback states
  affected by the change, rather than every possible input combination.
- Add or update representative scenarios in the
  [generator](../scripts/render_presentation_gallery.py) when a change adds or
  materially alters a rendered state. Use stable sample anchors or titles in QA
  notes; display numbers can change.
- Run focused regression tests for affected contracts, including captured-patch
  manual completion when review presentation changes. Verify changed metadata,
  operational markers, and parser semantics against source and tests, including
  evidence hidden by the rendered gallery. Freshness alone is not behavior coverage.

From the repository root, regenerate after changing a helper or sample input,
then check the committed output:

```sh
python3 -B scripts/render_presentation_gallery.py --write
python3 -B scripts/render_presentation_gallery.py --check
git diff --check
```

The generator makes no network, browser, or model calls. CI checks exact bytes
and fails on missing or stale output; it never regenerates the committed file.
Do not hand-edit the generated gallery or put observations or its own commit SHA
in it. Keep samples synthetic and free of reviewer-triggering invocation text.
The raw gallery retains complete synthetic production metadata; rendered HTML
comments must stay hidden. Markdown hard breaks use equivalent backslashes to
avoid trailing whitespace in Git diffs.

If emergency workflow presentation changes, also capture synthetic output from
both workflows through `ToolingFallbackScriptTests.run_fallback` in
[the workflow tests](../tests/test_claude_review_workflows.py), using stubbed
GitHub calls and temporary fixtures. Keep the matching outputs as labeled QA
adjuncts in the review evidence, with operational markers removed. This harness
is separate from the gallery generator; do not duplicate fallback templates or
append hand-edited output to the committed gallery.

Complete applicable local tests, gallery freshness, scope checks, and independent
review before authorized draft publication or a PR update. Documentation-only
changes that do not alter generated output need documentation, link, and
whitespace checks, not a repeated gallery interaction pass or full local suite.
The full regression suite remains a CI gate before merge, as described in
[repository instructions](../AGENTS.md#development-and-validation).

## 2. Inspect GitHub rendering

Use the built-in browser as the standard browser for the public synthetic
gallery. Open the published file in GitHub's rendered preview, not Raw or a local
Markdown preview. Record any different browser used and why. An alternate browser
is not a way around denied access.

Before declaring the presentation ready, verify that the generating checkout,
committed gallery, and published PR head match. Inspect the exact published
candidate during PR finalization. Local previews can help before publication,
but must be labeled separately and do not establish GitHub-rendered QA.

Check these two viewport widths, measured in CSS pixels rather than screenshot
pixels or the outer window size:

- Desktop: about **1280 px**.
- Narrow: about **390 px**. This is a responsive-layout check, not a physical-phone
  test or evidence of mobile-browser compatibility.

At each width, inspect the selected scenarios with the outer review summary,
Details, and nested About sections collapsed and expanded where present:

- Read the outcome, status, identity labels, tables, and warnings. Confirm that
  coverage and Check warnings remain visible when their review is collapsed.
- Check spacing, wrapping, clipping, and overflow. Text and controls must remain
  readable and reachable; intentional table scrolling must not hide content.
- Open, close, and reopen nested sections. Check that the hierarchy remains
  understandable and that expanding one section does not obscure adjacent content.
- Inspect link labels and destinations, including the real review-guide target.
  Synthetic commit, run, and finding links may intentionally have no live
  destination; verify their intended structure rather than requiring a successful
  network response or navigating to every fixture link.
- Confirm hidden receipts and operational markers do not appear in rendered text.

Capture representative desktop and narrow screenshots of changed views for the
task handoff. Identify their revision, viewport, and scenarios. Screenshots
supplement inspection; an image is not required for every example. If capture or
sizing is unavailable, record the precise gap instead of claiming it passed.

## 3. Check keyboard use and accessible structure

For each distinct changed interaction structure, include representative standing
and manual notices when both are affected. At both widths:

1. Use Tab and Shift+Tab to reach and leave the outer summary, Details, About,
   and links in expanded content. Confirm a visible focus indicator and a logical
   order with no trapped focus or unreachable control.
2. Use Enter and Space to expand and collapse each affected disclosure. Confirm
   the expected section changes, focus remains usable, and reopening works.
   After collapsing a section, keyboard navigation must not enter its hidden links.
3. Inspect the rendered HTML, and the browser accessibility snapshot when
   available, for meaningful disclosure names, the expected expanded/collapsed
   state, headings, table headers, list structure, and descriptive links. Check
   that About bullets and the separate guide paragraph retain their structure.
   Record which inspection method was available; do not claim accessibility-tree
   coverage from an HTML check alone.

These are focused keyboard and structural checks. They do not establish spoken
announcements, screen-reader reading order, touch interaction, or full
accessibility conformance.

## 4. Decide whether extra coverage is needed

The following are **not routine gallery checks**. Record them as not tested
unless actually performed for the candidate, or explain precisely why prior
coverage still applies:

- **Spoken screen-reader testing**, including VoiceOver. Use it when the change
  or a reported issue raises an announcement, reading-order, or assistive-navigation
  concern. Record the screen reader, browser, operating system, scenarios, and
  observed output. A DOM or accessibility-tree snapshot is not a spoken test.
- **Physical phones or tablets and other browser/OS combinations.** Use these
  for device-specific concerns or an explicit compatibility claim. A narrow
  desktop viewport or emulator must be labeled as such.
- **Live PR comments.** The gallery uses GitHub's file renderer; it does not
  establish identical behavior in GitHub's comment UI. Check an existing matching
  comment when authorized and useful. Posting fixtures, starting reviews, or
  exercising live workflows requires separate authorization and any applicable
  [live-run plan](../AGENTS.md#scope-and-runtime-adoption).

Omitting optional coverage alone does not block a routine presentation change.
A known defect or material uncertainty relevant to the change still needs to be
resolved or explicitly reported before recommending merge; do not dismiss it
merely because its test method is normally optional.

## 5. Record evidence and limitations

Keep the PR description concise, with one branch-based gallery preview link.
Record the exact inspected commit as plain text; a second commit-pinned gallery
link is not required. Include:

- Browser and actual CSS viewport widths.
- Inspected sample anchors or titles, collapsed/expanded states, and coverage
  rationale, including any reuse of prior evidence.
- Visual, keyboard, and structural results, with failures, blocked checks, and
  unrun checks distinguished. Name the structural inspection method.
- Representative screenshot locations in the task handoff and any capture limits.
- Extra coverage performed and explicit limits for spoken screen readers,
  physical devices, and live comments.

After a later edit, recheck affected output and interactions against the new
published head. Reuse evidence only for demonstrably unchanged output whose
behavior is unaffected by the change; identify the prior revision and explain
why that coverage carries forward. Do not paste the full gallery, duplicate the
Files changed diff, or include reviewer-triggering invocation text in QA evidence.

Routine read-only QA of this repository's local or public synthetic gallery is
part of an already authorized engineering task: sizing, opening and closing
Details, and screenshots do not need repeated separate approval. Platform and
assistant approval controls still apply. This scope does not authorize other
sites or interactions, sign-in or account settings, external publication, or
access/permission changes. Do not enable a screen reader or change system
settings without the necessary authorization.

If required GitHub-rendered coverage is unavailable or denied, report the exact
unverified checks and hold transitions that require them. Do not publish, switch
destinations, or change permissions to work around a browser denial. A blocked
check is not a passed check.
