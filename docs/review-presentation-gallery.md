# SAMPLE ONLY — visual QA gallery, not actual review results

All sample repository, commit, run, and timestamp identities are synthetic. Sample links may intentionally have no live destination. Operational Runtime markers are omitted; these samples are not review records.

Generated with `python3 -B scripts/render_presentation_gallery.py --write`. Edit the generator or sample inputs, then regenerate.

---

### A — Clean completion, matching baseline

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ✅ **Completed** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Result:** No findings.\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### A2 — Current findings completion, matching baseline

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ⚠️ **Findings** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

Assess the recorded findings in the inline review threads.

<details>
<summary>ℹ️ Details</summary>

<br/>

**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

**Synthetic inline finding context (separate from the status comment):**

`src/cache.py:42` — **[P2] Invalidate cached entries after deletion.** Deleting an item removes it from storage but leaves its cached value intact, so subsequent reads can return the deleted item until the cache expires. Invalidate the cache entry after a successful deletion.

---

### B — Clean completion, changed target branch

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ✅ **Completed** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

⚠️ The target branch differs from the reviewed baseline. Integration with its current version hasn’t been reviewed.

<details>
<summary>ℹ️ Details</summary>

<br/>

<!--
**Reason:** The target baseline differs; integration with the current baseline has not been reviewed.
-->

**Result:** No findings.\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
**Current target commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main`

</details>

---

### C — Findings, changed target branch

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ⚠️ **Findings** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

Assess the recorded findings in the inline review threads.

⚠️ The target branch differs from the reviewed baseline. Integration with its current version hasn’t been reviewed.

<details>
<summary>ℹ️ Details</summary>

<br/>

<!--
**Reason:** The target baseline differs; integration with the current baseline has not been reviewed.
-->

**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
**Current target commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main`

</details>

---

### D — PR commit changed after clean completion

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ✅ **Completed** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

⚠️ This review covers commit [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa). The current PR commit [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) has not been reviewed by it.

<details>
<summary>ℹ️ Details</summary>

<br/>

<!--
**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.
-->

**PR commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main` — not reviewed\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### E — Incomplete attempt without completed history

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

The review did not complete with reliable execution and finding evidence. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The review did not complete with reliable execution and finding evidence.

**Started:** <relative-time datetime="2026-10-07T13:00:00Z">2026-10-07T13:00:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### F — Compact clean completion, matching baseline

Claude Review: No findings. 🎉

**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)

<details>
<summary>ℹ️ Details</summary>

<br/>

**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### G — Compact clean completion, differing baseline

Claude Review: No findings. 🎉

**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)

⚠️ The target branch differs from the reviewed baseline. Integration with its current version hasn’t been reviewed.

<details>
<summary>ℹ️ Details</summary>

<br/>

**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
**Observed target commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main`

</details>

---

### H — Running attempt with older completed history: clean

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Current attempt**\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### H — Running attempt with older completed history: findings

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Current attempt**\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** Findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### I — Incomplete attempt with older completed history: clean

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

The review did not complete with reliable execution and finding evidence. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The review did not complete with reliable execution and finding evidence.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### I — Incomplete attempt with older completed history: findings

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

The review did not complete with reliable execution and finding evidence. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The review did not complete with reliable execution and finding evidence.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** Findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### J — Retargeted provenance: clean

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ✅ **Completed** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

⚠️ This review was against `main`. It does not cover the PR’s current target `release`.

<details>
<summary>ℹ️ Details</summary>

<br/>

<!--
**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.
-->

**PR commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `release` — not reviewed\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
**Current target commit:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `release`

</details>

---

### J — Retargeted provenance: findings

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ⚠️ **Findings** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

⚠️ This review was against `main`. It does not cover the PR’s current target `release`.

Assess the recorded findings in the inline review threads.

<details>
<summary>ℹ️ Details</summary>

<br/>

<!--
**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.
-->

**PR commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `release` — not reviewed\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
**Current target commit:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `release`

</details>

---

### K — Authoritative Check publication incomplete: no completed history

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

The authoritative Check result could not be published. Treat this commit as not reviewed. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The authoritative Check result could not be published.

**Started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

</details>

---

### K — Authoritative Check publication incomplete: previous clean review

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

The authoritative Check result could not be published. Treat this commit as not reviewed. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The authoritative Check result could not be published.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### K — Authoritative Check publication incomplete: previous findings

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

The authoritative Check result could not be published. Treat this commit as not reviewed. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The authoritative Check result could not be published.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** Findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### L — Completion with unavailable Check publication: no completed history

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

Review completion cannot be established because authoritative Check publication is unavailable. Consult the workflow run and inline review threads. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The authoritative Check result could not be published.

**Started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

</details>

---

### L — Completion with unavailable Check publication: previous clean review

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

Review completion cannot be established because authoritative Check publication is unavailable. Consult the workflow run and inline review threads. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The authoritative Check result could not be published.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### L — Completion with unavailable Check publication: previous findings

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

Review completion cannot be established because authoritative Check publication is unavailable. Consult the workflow run and inline review threads. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The authoritative Check result could not be published.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** Findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### M — Running with unavailable Check publication: no completed history

## Claude Review

Claude is reviewing this commit, but authoritative Check publication is unavailable.

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

</details>

---

### M — Running with unavailable Check publication: previous clean review

## Claude Review

Claude is reviewing this commit, but authoritative Check publication is unavailable.

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Current attempt**\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### M — Running with unavailable Check publication: previous findings

## Claude Review

Claude is reviewing this commit, but authoritative Check publication is unavailable.

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Current attempt**\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** Findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### S — Required inputs unread, automatic attempt

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

Required review inputs were not fully read. [Recovery guidance](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#incomplete-captured-input-reads).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** Required review inputs were not fully read.

**Started:** <relative-time datetime="2026-10-07T13:00:00Z">2026-10-07T13:00:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### T — Required inputs unread, manual attempt with workflow changes

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Manual request |

Required review inputs were not fully read. [Recovery guidance](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#incomplete-captured-input-reads).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** Required review inputs were not fully read.

**Started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

</details>

---

### U — Relevant review workflow changed, cause unknown

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | PR opened for review |

⚠️ The captured patch changes the review workflow, which may prevent the review from starting. Check the [workflow run](https://github.com/owner/repo/actions/runs/5/attempts/1) for a validation rejection. [Recovery guidance](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The captured patch changes the review workflow; the cause of the incomplete review is unknown.

**Started:** <relative-time datetime="2026-10-07T13:00:00Z">2026-10-07T13:00:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### V — Workflow-change context retained for a superseded attempt

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | Not recorded |

Current head and base coverage is not established by this attempt.

⚠️ The captured patch changes the review workflow, which may prevent the review from starting. Check the [workflow run](https://github.com/owner/repo/actions/runs/5/attempts/1) for a validation rejection. [Recovery guidance](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt. The captured patch changes the review workflow; the cause of the incomplete review is unknown.

**PR commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main` — not reviewed\
**Started:** <relative-time datetime="2026-10-07T13:00:00Z">2026-10-07T13:00:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### H — Manual current attempt with previous completed review: in_progress

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Manual request |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Current attempt**\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
This review covers the captured patch only; integration with other target baselines is not covered.

</details>

---

### I — Manual current attempt with previous completed review: failure

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Manual request |

The review did not complete with reliable execution and finding evidence. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The review did not complete with reliable execution and finding evidence.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** Findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
This review covers the captured patch only; integration with other target baselines is not covered.

</details>

---

### K — Manual current attempt with previous completed review: publication_incomplete

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Manual request |

The authoritative Check result could not be published. Treat this commit as not reviewed. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The authoritative Check result could not be published.

**Current attempt**\
**Attempt started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
This review covers the captured patch only; integration with other target baselines is not covered.

</details>

---

### N — Shared workflow run, matching historical baseline: in_progress

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **[Running](https://github.com/owner/repo/actions/runs/5)** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | PR opened for review |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### O — Shared workflow run, matching historical baseline: failure

## Claude Review

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| ❌ **Incomplete** | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | PR opened for review |

The review did not complete with reliable execution and finding evidence. [Troubleshoot incomplete reviews](https://github.com/JBallin/claude-review-runtime/blob/main/docs/review-guide.md#installation-and-incomplete-reviews).

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The review did not complete with reliable execution and finding evidence.

**Current attempt**\
**[Attempt](https://github.com/owner/repo/actions/runs/5) started:** <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time>

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### P — Legacy completed history without recorded attempt metadata

## Claude Review

**Last completed review**

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ✅ **Completed** | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | Not recorded |

Current head and base coverage is not established by this attempt.

<details>
<summary>ℹ️ Details</summary>

<br/>

**[Attempt](https://github.com/owner/repo/actions/runs/6) status:** Not recorded\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) target:** `main`

</details>

---

### Q — Legacy completed review without workflow links

## Claude Review

| Status | Reviewed commit | Review trigger |
| --- | --- | --- |
| ✅ **Completed** | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | Not recorded |

⚠️ This review covers commit [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa). The current PR commit [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) has not been reviewed by it.

<details>
<summary>ℹ️ Details</summary>

<br/>

<!--
**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.
-->

**PR commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main` — not reviewed\
**Reviewed against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### R — Stale running attempt without completed history: changed head

## Claude Review

Claude is reviewing captured commit [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa). Review coverage of the current head and baseline is not established.

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:00:00Z">2026-10-07T13:00:00Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.

**PR commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main` — not reviewed\
**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`

</details>

---

### R — Stale running attempt without completed history: changed base

## Claude Review

Claude is reviewing captured commit [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa). Review coverage of the current head and baseline is not established.

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:00:00Z">2026-10-07T13:00:00Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.

**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
**Current target commit:** [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) on `main`

</details>

---

### R — Stale running attempt without completed history: changed ref

## Claude Review

Claude is reviewing captured commit [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa). Review coverage of the current head and baseline is not established.

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:00:00Z">2026-10-07T13:00:00Z</relative-time> | [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) | Draft marked ready |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.

**PR commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `release` — not reviewed\
**[Attempt](https://github.com/owner/repo/actions/runs/5) baseline:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
**Current target commit:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `release`

</details>

---

### R — Stale manual attempt with distinct completed-review trigger

## Claude Review

Claude is reviewing captured commit [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb). Review coverage of the current head and baseline is not established.

| Status | Attempt commit | Review trigger |
| --- | --- | --- |
| 🔄 **Running** started <relative-time datetime="2026-10-07T13:10:00Z">2026-10-07T13:10:00Z</relative-time> | [`bbbbbbb`](https://github.com/owner/repo/commit/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb) | Manual request |

<details>
<summary>ℹ️ Details</summary>

<br/>

**Reason:** The captured head or base was superseded; the current patch is not reviewed by this attempt.

**Current attempt**\
**[Attempt](https://github.com/owner/repo/actions/runs/6) baseline:** [`eeeeeee`](https://github.com/owner/repo/commit/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee) on `main`

**Previous completed review**\
**Result:** No findings.\
**Reviewed commit:** [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa) on `main`\
**Completed:** <relative-time datetime="2026-10-07T13:09:19Z">2026-10-07T13:09:19Z</relative-time>\
**Review trigger:** PR opened for review\
**[Reviewed](https://github.com/owner/repo/actions/runs/5) against:** [`ddddddd`](https://github.com/owner/repo/commit/dddddddddddddddddddddddddddddddddddddddd) on `main`\
This review covers the captured patch only; integration with other target baselines is not covered.

</details>
