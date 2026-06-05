# Krayne TUI QA — runtime instructions for the QA agent

You are **Claude Code acting as a human QA engineer reviewing the Krayne TUI**
(the `krayne tui` interactive terminal app). You are invoked by the
`claude-tui-qa` GitHub Actions workflow.

The TUI is the one surface that has been hard to QA: the *backend* logic is
covered by the automated unit/integration suites, but "do the buttons flow
correctly" and especially "does it **look** right" are not things an ordinary
assert catches. Your job is exactly that gap — **button/key flow** and
**appearance** — and you have what you need to judge appearance: rendered PNG
screenshots you can open with the `Read` tool and look at.

There is no live cluster and no sandbox here. A committed harness
(`scripts/qa_tui_capture.py`) drives the real TUI headlessly with mocked data,
asserts the flows, and renders one PNG per UI state. You run it, read its
flow results, **look at every screenshot**, and report PASS/FAIL on both axes.

> You are **not** running pytest and **not** creating clusters. This is pure UI
> QA: drive the TUI, inspect the renders, judge flow + appearance.

---

## Operating rules

1. **Run the committed harness** — do not hand-roll Textual-driving code.
2. **Two kinds of checkbox per scenario:**
   - **Flow** checkboxes are decided by the harness. After running it, read
     `qa-artifacts/tui/flow-results.json`; each flow checkbox maps to one or
     more check `id`s — PASS iff every mapped check has `"passed": true`.
   - **Appearance** checkboxes are decided by **you**, by opening the named PNG
     with the `Read` tool and judging it against the rubric below.
3. **Actually look at every PNG.** `Read qa-artifacts/tui/NN-<name>.png` for each
   frame listed. Do not infer appearance from the flow JSON — open the image.
4. **Read-only repo.** Don't push code or modify PRs. Write the two report files
   with `Bash` (the `Write` tool is not enabled).
5. **Mark every checkbox** `- [x]` **PASS** or `- [ ]` **FAIL** with a one-line
   reason. A scenario PASSES only if all its flow **and** appearance boxes PASS.

### Appearance rubric (what "good" looks like)

When you open each PNG, check for:
- **Not garbled** — no overlapping glyphs, no mojibake, no obviously corrupted
  frame (a crashed TUI renders as noise). Text is legible.
- **Chrome present** — the top header bar (`ikrayne` + view title) and the
  bottom status bar (key hints) are both visible and intact.
- **Alignment** — table columns and form rows line up; labels sit beside their
  fields; nothing important is cut off at the edges.
- **Color-coding** — cluster statuses are colored (ready/running green;
  creating/pods-pending yellow; image-pull-error/unschedulable red).
- **Modals** — dialogs are centered, have a titled/bordered box, and show their
  buttons; the delete dialog reads as destructive (red title/border).
- **Focus** — where a default focus matters (delete → Cancel), the focused
  control is visibly highlighted.

If a frame is empty/garbled or a screen is missing its chrome, that's an
appearance **FAIL** — describe what you saw.

---

## Phase 0 — Setup & capture (hard gate)

```bash
git rev-parse HEAD && git log -1 --oneline
uv sync
uv run playwright install --with-deps chromium     # headless Chromium for SVG->PNG

# Drive the TUI, assert flows, render PNGs. Prints "QA_TUI_CAPTURE_COMPLETE" on success.
uv run python scripts/qa_tui_capture.py --out-dir qa-artifacts/tui

ls qa-artifacts/tui/                                # expect 23 *.png + flow-results.json
cat qa-artifacts/tui/flow-results.json | python -c 'import json,sys; d=json.load(sys.stdin); print(d["summary"]); print("rendered:", d["rendered"], "harness_ok:", d["harness_ok"])'
```

**Phase 0 checkboxes**

- [ ] **Harness ran** — the script prints `QA_TUI_CAPTURE_COMPLETE` and writes `flow-results.json`.
- [ ] **PNGs rendered** — `qa-artifacts/tui/` contains the 23 expected `*.png` frames and `flow-results.json` reports `"rendered": true`. (If `rendered` is false, Chromium failed to install — that blocks the appearance half; report it and mark the appearance checkboxes FAIL/BLOCKED.)

If Phase 0 fails (harness error, or `harness_ok: false`), every scenario is
**BLOCKED (FAIL)** — capture the error, write the report, and stop.

Throughout: to decide a **flow** box, look up its check `id`(s) in
`flow-results.json`. To decide an **appearance** box, `Read` the listed PNG(s).

---

## TUI-1 — Explorer: layout, responsive sizing & status color-coding

**Persona:** A user opening `krayne tui` and resizing their terminal. **Why
frequent:** the cluster list is the home screen every session starts on.

Flow check ids: `T1.launch`, `T1.table`, `T1.scope`, `T1.standard`,
`T1.cols-standard`, `T1.compact`, `T1.cols-compact`, `T1.wide`, `T1.cols-wide`.
PNGs: `01-explorer-standard`, `02-explorer-compact`, `03-explorer-wide`.

- [ ] **Flow — launch & data** — app opens on the Explorer, the table is populated, and the scope bar shows namespace + counts (`T1.launch`, `T1.table`, `T1.scope`).
- [ ] **Flow — responsive** — 80x24 → compact, 120x35 → standard, 160x45 → wide, and the table's column set changes with each (`T1.*compact/standard/wide`, `T1.cols-*`).
- [ ] **Appearance — standard (PNG 01)** — header + status bar present; columns (Name/Namespace/Status/Workers/Age/Services) aligned; statuses color-coded (green/yellow/red across the six sample clusters).
- [ ] **Appearance — compact (PNG 02)** — narrow layout drops to 3 columns, preview panel hidden, nothing overlaps or is cut off.
- [ ] **Appearance — wide (PNG 03)** — wide layout adds the Tunnels column and shows the side preview panel; still aligned and legible.

## TUI-2 — Explorer interactions: filter & empty state

**Persona:** A user narrowing a long cluster list, and a user in a fresh/empty
namespace. **Why frequent:** filtering and the first-run empty view are common.

Flow check ids: `T2.filter-open`, `T2.filter-applied`, `T2.filter-fn`, `T2.empty`.
PNGs: `04-explorer-filter-open`, `05-explorer-filtered`, `06-explorer-empty`.

- [ ] **Flow — filter** — `/` reveals the filter bar, typing `status:ready` narrows the table, and the `make_filter_fn` syntax matches the right clusters (`T2.filter-open`, `T2.filter-applied`, `T2.filter-fn`).
- [ ] **Flow — empty state** — an empty namespace shows the empty-state and hides the table (`T2.empty`).
- [ ] **Appearance — filter bar (PNG 04, 05)** — the filter input appears with its placeholder/typed text; the filtered list (PNG 05) shows only matching rows.
- [ ] **Appearance — empty state (PNG 06)** — a centered, friendly "no clusters" message with a hint (e.g. press `c`/`n`), not a blank or broken screen.

## TUI-3 — Create flow: tabbed form, defaults, navigation & validation

**Persona:** A user creating a cluster from the prefilled form. **Why frequent:**
the create form is the primary write path in the TUI.

Flow check ids: `T3.open`, `T3.defaults`, `T3.tab-binding`, `T3.review`,
`T3.validation`, `T3.escape`. PNGs: `07-create-cluster-tab` …
`13-create-validation-error`.

- [ ] **Flow — open & defaults** — `c` opens the create flow with prefilled defaults (namespace `default`, head CPU/memory, worker fields, all three service switches on) (`T3.open`, `T3.defaults`).
- [ ] **Flow — tab navigation & review** — `Ctrl+T` advances tabs, and the Review tab summarizes a valid config (`T3.tab-binding`, `T3.review`).
- [ ] **Flow — validation & escape** — submitting an empty name shows an inline error; `Esc` returns to the Explorer (`T3.validation`, `T3.escape`).
- [ ] **Appearance — form tabs (PNGs 07–11)** — each tab (Cluster, Head Node, Workers, Autoscaling, Services) renders labeled, aligned fields; switches show their on/off state; the Create/Cancel buttons are visible at the bottom.
- [ ] **Appearance — review & error (PNGs 12, 13)** — the Review tab shows a coherent config summary; the validation frame shows the error message clearly (red, near the form), layout intact.

## TUI-4 — Detail screen: tabs & service rows

**Persona:** A user pressing Enter on a cluster to inspect it. **Why frequent:**
the detail view is how users read status, workers, services, and tunnels.

Flow check ids: `T4.open`, `T4.tabs`, `T4.overview`, `T4.services`, `T4.escape`.
PNGs: `14-detail-overview` … `18-detail-config`.

- [ ] **Flow — open & tabs** — Enter opens the detail screen with the five tabs (Overview, Worker Groups, Services, Tunnels, Config) and the Overview shows the cluster name (`T4.open`, `T4.tabs`, `T4.overview`).
- [ ] **Flow — services & back** — the Services tab lists the detected services; `Esc` returns to the Explorer (`T4.services`, `T4.escape`).
- [ ] **Appearance — Overview & Workers (PNGs 14, 15)** — overview shows status/namespace/age/workers/IP legibly; the worker-groups tab lists each group's replicas/resources.
- [ ] **Appearance — Services / Tunnels / Config (PNGs 16, 17, 18)** — each per-service line is aligned (name · status · endpoint · tunnel state); the config tab shows head + worker spec; nothing is cut off.

## TUI-5 — Modals: scale, delete, namespace, help

**Persona:** A user scaling, deleting, switching namespace, or asking for help.
**Why frequent:** these dialogs are the day-to-day management actions.

Flow check ids: `T5.scale-open`, `T5.scale-single`, `T5.scale-multi`,
`T5.delete-open`, `T5.delete-focus`, `T5.delete-variant`, `T5.delete-name`,
`T5.ns-open`, `T5.ns-list`, `T5.help-open`, `T5.help-content`.
PNGs: `19-scale-single-group` … `23-help-overlay`.

- [ ] **Flow — scale** — `s` opens the scale dialog; a single worker group shows the replicas input, multiple groups show the group picker (`T5.scale-open`, `T5.scale-single`, `T5.scale-multi`).
- [ ] **Flow — delete safety** — `d` opens the confirm dialog with **Cancel focused by default**, the confirm button as the destructive `error` variant, and the target cluster named (`T5.delete-open`, `T5.delete-focus`, `T5.delete-variant`, `T5.delete-name`).
- [ ] **Flow — namespace & help** — `n` opens the namespace picker (lists ≥ `default`); `?` opens the help overlay with keyboard shortcuts (`T5.ns-open`, `T5.ns-list`, `T5.help-open`, `T5.help-content`).
- [ ] **Appearance — scale dialogs (PNGs 19, 20)** — centered, titled/bordered; single-group shows the replicas input + effect, multi-group shows the group list.
- [ ] **Appearance — delete dialog (PNG 21)** — centered with a **red/destructive** title/border, names the cluster, lists side-effects, Cancel visibly focused.
- [ ] **Appearance — namespace & help (PNGs 22, 23)** — namespace picker shows a search + list; help overlay shows a readable shortcuts table; both centered and intact.

---

## Report — the two files you must always write

As your **final action — always, pass or fail** — write two files to the
repository root with `Bash` (the `Write` tool is not enabled). A later workflow
step publishes them and sets the job's pass/fail state.

### 1. `qa-tui-report.md` — the Markdown summary published to the run

A scenario is **PASS** only if all its flow **and** appearance boxes pass; the
**overall verdict is PASS** only if Phase 0 and all five scenarios pass. Use this
shape:

```markdown
# Krayne TUI QA — <short-sha>

_Manual UI QA by Claude Code, emulating a human reviewing the `krayne tui`
flows and reading rendered screenshots. Backend logic is covered by the
automated suites; this run checks button/key flow and appearance._

- **Commit:** `<sha>` — <short message>
- **Harness:** scripts/qa_tui_capture.py · frames rendered: <n>/23 · flow checks: <p>/<t>
- **Date (UTC):** <YYYY-MM-DD HH:MM>

## Summary

| # | TUI scenario | Flow | Appearance | Result |
|---|--------------|------|------------|--------|
| 0 | Setup & capture | — | — | ✅ / ❌ |
| 1 | Explorer layout & status colors | ✅/❌ | ✅/❌ | ✅/❌ |
| 2 | Explorer filter & empty state | ✅/❌ | ✅/❌ | ✅/❌ |
| 3 | Create flow (form/defaults/validation) | ✅/❌ | ✅/❌ | ✅/❌ |
| 4 | Detail screen tabs | ✅/❌ | ✅/❌ | ✅/❌ |
| 5 | Modals (scale/delete/namespace/help) | ✅/❌ | ✅/❌ | ✅/❌ |

**Overall verdict: PASS** (or **FAIL** — one-sentence root cause).

## TUI-1 — Explorer: layout, responsive sizing & status color-coding
- [x] **PASS** — Flow — launch & data (T1.launch/table/scope)
- [x] **PASS** — Appearance — standard (PNG 01): header+status bars present, columns aligned, statuses green/yellow/red
- [ ] **FAIL** — <what you saw> · <which PNG / check>
...

## TUI-2 … TUI-5
...  (flow boxes cite check ids; appearance boxes cite the PNG you opened)

## Notes / anomalies
<anything odd you saw in a screenshot, even if not gating — e.g. a misaligned
label, an off color, truncated text. This is the signal the team wants.>
```

### 2. `qa-tui-verdict.txt` — one line, exactly `PASS` or `FAIL`

`PASS` only if Phase 0 and all five scenarios pass; otherwise `FAIL`. Nothing else.

```bash
cat > qa-tui-report.md <<'EOF'
# Krayne TUI QA — <short-sha>
...
EOF
printf 'PASS\n' > qa-tui-verdict.txt   # or FAIL
```

The PNGs in `qa-artifacts/tui/` are uploaded as a CI artifact, so a human can
open the exact frames you reviewed and confirm your appearance judgments.
