# Review presentation QA

Use this checklist when generated review output changes. For documentation-only
changes, follow the [normal validation rules](../AGENTS.md#development-and-validation).

## Before publication

- Update [generator scenarios](../scripts/render_presentation_gallery.py) for
  distinct changed states, including affected errors and fallbacks. Keep samples
  synthetic and free of reviewer invocation text; do not hand-edit the gallery.
- Run focused regression tests and check changed operational metadata against
  source and tests. Gallery freshness does not establish behavior coverage.
- Regenerate, inspect the diff, and check freshness from the repository root:

  ```sh
  python3 -B scripts/render_presentation_gallery.py --write
  python3 -B scripts/render_presentation_gallery.py --check
  git diff --check
  ```

- For emergency workflow presentation changes, capture both workflows through
  `ToolingFallbackScriptTests.run_fallback`. Keep labeled outputs with operational
  markers removed in QA evidence, separate from the generated gallery.
- Complete applicable local validation, scope checks, and independent review
  before authorized publication or updates. Full-suite CI remains a merge gate.

## Published gallery checklist

Use the built-in browser to inspect the [gallery](review-presentation-gallery.md)
in GitHub's rendered preview at the exact published PR head. Verify it matches
the generating checkout. Local previews do not replace this check.

- Check desktop (~1280) and narrow (~390) **CSS viewport pixels**. Cover changed
  states plus representative unchanged cases; inspect every section when shared
  layout changes could affect the whole gallery.
- Open, close, and reopen each affected summary, Details, and About section.
  Check wrapping, spacing, tables, warnings, identity labels, and link targets.
  Required warnings must remain visible when collapsed; receipts must stay hidden.
- At both widths, use Tab/Shift+Tab and Enter/Space. Check visible focus, logical
  order, reachable controls, no focus traps, and no focus on collapsed content.
- Inspect rendered HTML and, when available, the accessibility tree for disclosure
  names/states, headings, table headers, lists, and links. Record which method ran.
- Capture representative desktop and narrow screenshots, labeled with the revision
  and scenarios. Complete this pass before marking ready or recommending merge.

## Evidence and limits

In the PR description, include one branch-based gallery link, the inspected SHA
as text, browser, actual widths, scenarios, results, and any blocked/unrun checks.
Include screenshot locations in the handoff; do not paste the gallery or diff.
After edits, recheck affected output; cite and explain any reused prior evidence.

Spoken screen-reader testing (including VoiceOver), physical devices, other
browser/OS combinations, and live PR-comment rendering are **not routine checks**.
List them as untested unless performed. Use separate checks for relevant defects
or compatibility claims; a narrow viewport or DOM check does not establish them.
Known defects or material uncertainty still need resolution or explicit disclosure.

Routine sizing, disclosures, and screenshots of this synthetic gallery are within
an authorized engineering task; platform approvals still apply. This excludes
other sites, sign-in, settings/access changes, and publication. Never bypass denied
access; report missing coverage and hold transitions requiring it. Live workflow
exercises follow the [separate approval requirements](../AGENTS.md#scope-and-runtime-adoption).
