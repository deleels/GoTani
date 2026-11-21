# Guide: a test-and-optimise session for GoTani

How to start a fresh Claude Code session that tests the whole app, checks it against
GoTani-design-system, fixes what it finds and makes it faster. It uses the skills already installed.

---

## 1. Before you start

1. **Choose the base branch.** The redesign is still on `redesign/design-system` (PR deleels/GoTani#2).
   - Merge the PR first if you're happy with it, and the new session works from `main`.
   - If you'd rather not merge yet, start from `redesign/design-system`.
   - Don't start from an old `main`: it doesn't have the redesign.
2. **Open the session in a worktree.** In the desktop app, use New session → folder `gotani` → turn on **worktree**.
   The session then gets its own branch (for example `optimise/round-1`) and can't break your working copy.
3. **Model and effort.** Use Opus with **high** effort for the audit and fixes. Read-only side jobs, such as sweeping
   every screen for console errors, can run on a cheaper model in a subagent.
4. **Allow the browser pane** for `localhost`. The session tests in it.

## 2. The prompt to paste

Copy the block below into the new session as its first message. Change the `Focus` line if you only want part of it.

```text
Test and optimise GoTani against GoTani-design-system. Follow docs/OPTIMIZE_SESSION.md sections 3–7.

Focus: whole app (all roles: OWNER, CASHIER), phone 390px and desktop 1440px.

Setup:
- Make a scratch sqlite DB in your scratchpad, run migrate + seed_demo + check_mass_balance on it,
  and add a launch.json entry "gotani-test" on port 8020 that uses it. Never touch the real DB.
- Design system source: C:\Users\derry\Documents\UI Inspo\GoTani inspo\GoTani-design-system
  (README.md is the rulebook; components/ are the reference).

Rules:
- Never edit core/static/core/ds/ (copied DS files). Overrides go in core/static/core/gotani.css.
- Keep URLs, form field names, Alpine component names and data-* hooks.
- Don't change user credentials. Don't commit .agents/, .claude/ or skills-lock.json.
- One commit per fix, tests passing each time. Don't push until I say so.
- Plan first: show me the findings list ranked by impact before fixing anything.

Skills: ui-ux-pro-max and gotani:design-taste-frontend (app-screen mode) for the audit,
dataviz for charts, simplify + code-review high + security-review before the PR.
```

## 3. Phase 1: baseline (does it work?)

The session should run these, record the results and change nothing yet.

| Check | Command or method |
|---|---|
| Unit tests | `PYTHONUTF8=1 .venv/Scripts/python.exe manage.py test` (377 tests now, 2 skipped without Node) |
| Data integrity | `manage.py check_mass_balance` on the seeded DB |
| Missing translations | `manage.py i18n --missing` |
| Every screen loads | Visit every page in the nav as OWNER and as CASHIER. Note console errors and any 4xx/5xx in network requests. |
| No sideways scroll | At 390, 768 and 1440 px: `document.documentElement.scrollWidth > innerWidth` must be false |
| Key flows end to end | POS sale → receipt; harvest → stock; invoice → part payment → credit; expense approve/reject; draw and edit a field polygon; change password |

Ask it for a short **baseline report**: what passed, what broke, plus screenshots of anything broken.

## 4. Phase 2: design-system audit

The session compares every screen against the README rules. Useful checks:

- **Action hierarchy:** one gradient `primary` button per page, at the top only. Form and dialog submits are
  flat `solid` pills. Secondary actions are `outline`. Every form has a Back/Batal.
- **Hatching** means only *pending, target or not yet*.
- **Charts:** fat pill bars with varied fills and no identical rows. Share of a whole is a half-donut Gauge, never a
  doughnut. Axes use short money (`Rp2 jt`); tooltips show the exact amount. The `dataviz` skill applies here.
- **Footnotes** are (i) InfoTip chips, never grey small print.
- **No native widgets:** themed DatePicker, custom validation messages, thin pill scrollbars.
- **Shape and type:** card radius 20, tile radius 12, pills everywhere. Only Plus Jakarta Sans, with numbers as the heroes.
- **Depth:** flat. Only floating things have a shadow.
- **Copy:** verb + noun actions. Use the fixed status words (Completed / In Progress / Pending, On plan / Watch / At risk).
  Money is `Rp50.000.000` in forms and `Rp277,5 jt` in dashboards. No emoji.
- **Your own preferences** (from earlier rounds):
  - All green surfaces lit from one light source.
  - Flat buttons with no seam line.
  - Nothing covering the map.
  - Mixed English/Indonesian where it reads better ("Install", LTGP, Gross profit).

The output is a **findings list** ranked by impact. Each finding has a screen, a screenshot, the rule it breaks and
a suggested fix. You choose what gets fixed. Saying "yes to all except N" works well here.

## 5. Phase 3: performance

| Area | What to measure | Typical fix |
|---|---|---|
| Server | Query count per page (`django.test.utils.CaptureQueriesContext` or `assertNumQueries` in a test) | `select_related` / `prefetch_related`, aggregate in SQL |
| Page weight | Transferred bytes per page in network requests; size of `tailwind.css`, `gotani.css`, JS | Drop unused CSS, then rebuild Tailwind (command in CLAUDE.md) |
| Load | `performance.getEntriesByType('navigation')`, plus LCP from a `PerformanceObserver` | Preload what's above the fold; defer Leaflet and Chart.js to the pages that use them |
| Interaction | Nav taps, page-to-page transitions, chart redraws | Debounce, avoid double navigation, no layout shift |
| Service worker | Stale pages after a deploy | Bump the cache version; check that the update flow works |

For each optimisation, ask for the number **before and after**.

## 6. Phase 4: fix loop

For each approved finding:
1. Fix it in the smallest change, in the right file (`gotani.css`, the template, or the view).
2. Check it in the browser at 390 and 1440. Take a screenshot.
3. Run the tests. Then commit, one finding per commit.
4. If it was a design decision, add a `D1xx` entry to `docs/DECISIONS.md`.

## 7. Phase 5: review and hand-over

1. `/simplify`: cleans up the changed code.
2. `/code-review high`: looks for bugs in the diff.
3. `/security-review`: forms, permissions (the CASHIER scope), uploads.
4. Rerun the Phase 1 baseline and compare it with the first run.
5. Write the PR description: changes, migrations, checks run, and what is not yet checked. Push and open the PR only once you say so.

## 8. Which skill, when

| Skill | Use it for | Notes |
|---|---|---|
| `ui-ux-pro-max` | Audit rules: accessibility, interaction, layout, forms | Search it for the specific problem (forms, tables, charts) |
| `gotani:design-taste-frontend` | Look-and-fix loop on screenshots, app-screen mode | Use the gotani-scoped one, not the unscoped one |
| `dataviz` | Any chart change | Read it before touching Chart.js code |
| `grilling` / `grill-me` | Settling open questions before code | Good for Phase 2 decisions; `grill-me` only runs when called by name |
| `simplify`, `code-review`, `security-review` | Phase 5 | `code-review ultra` is a paid cloud review; you start it yourself |
| `run` | Starting and driving the app | Optional; the launch.json entry already covers this |
| `minimalist-ui` | **Don't use** | Its warm monochrome style clashes with the green design system |

## 9. Known gotchas (tell the session if it gets stuck)

- **Service-worker cache serves old CSS or pages.**
  - Unregister the service worker and clear its caches.
  - Then load the page with `?t=<timestamp>`, or fetch the file with `{cache: 'reload'}`.
- **Leaflet and Alpine:** never store Leaflet objects in Alpine state. The proxies break zoom and editing.
- **Django `{# #}` comments** are one line only. Use `{% comment %}` for more than one line.
- **Gradients on bordered elements** need `background-origin: border-box`, or a seam line shows.
- **Tailwind is prebuilt.** New utility classes do nothing until it's rebuilt.
- **JS strings** use `{% jt %}`. New Indonesian strings need English in `locale/en/tr_*.py`.

## 10. Still unchecked from the last round

Good first targets for this session:
- NDVI image and legend with a real Agromonitoring key.
- Print layouts for receipts and invoices.
- The login page and the forced-password dialog.
- Long-press to delete a polygon corner on a real phone.
- The farm location is still the demo point in Bogor. Set the real coordinates in Settings.
