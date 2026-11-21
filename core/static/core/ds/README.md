# GoTani

GoTani is the design system for GoTani, a calm, green commerce dashboard for farm-supply stores: white rounded cards floating on soft grey panels, one confident brand green, big friendly numbers and pill-shaped everything. Colours, radii and type sizes come from the reference dashboard (sampled from its pixels, measured on its 1440px frame); the component set was extended so every screen in the reference product walkthrough (dashboard, detail page, record dialog, review-and-sign form, passkey prompts, mobile payment and status flow) can be rebuilt from this system.

## Content fundamentals

- **Voice:** short, plain, reassuring. Say what happens and when: *"Nothing is final until the supplier accepts."* *"If it doesn't ship by Fri 4 Dec, escrow refunds you in 5 days."*
- **Greeting + context** on overview pages: *Good morning, Mirza* / *Friday 13 November. Prices as of 30 Sep.*
- **Titles** are Title Case nouns on cards (Order Analytics, Team Collaboration); **headings inside flows** are sentence case and specific (*Pay Rp50.000.000 by Mon 23 Nov*).
- **Actions** are verb + noun: *Add Product*, *Sign documents*, *Ask a question*. Small in-card creation is just *+ New*. After acting, the button says what happened: *Transfer started*.
- **Deadlines lead:** eyebrows put the date first — *Reorder by Tue 1 Dec*, *Expires Wed 2 Dec*.
- **Fixed status words:** Completed / In Progress / Pending for tasks; On plan / Watch / At risk for health. Use them verbatim.
- **Money:** `Rp50.000.000` in forms and receipts (exact); `Rp277,5 jt` / `Rp593rb` in dashboards and charts (abbreviated). Dates *Mon 16 Nov*; times *5 pm WIB*. No emoji.

## Visual foundations

**Colour.** One green family carries the brand: `green-900` (immersive dark cards), `green-800`/`green-700` (primary fills, selected states, counts), `green-600` (logo, active nav, chart bars, focus), `green-400` (highlighted bar), `green-100` (tints). `lime-400` is only for positive trends on dark green. Neutrals step `canvas` → `surface` (panels, tiles) → `surface-raised` (cards). Text is `ink` black with `ink-muted` and `ink-subtle`. Yellow (`highlight`, `warning-*`) marks things to weigh; red (`pending-*`, `danger`) marks risk — both stay small.

**One light.** Every green surface (the primary button, the HeroCard, the featured StatCard) is lit by one light on the right of the page: `--gt-lit`, a radial `#3f9d6b` → `green-600` 28% → `green-800` 72% → `green-900`. The glow's corner follows where the surface sits: at the top of a page (page head, HeroCard, featured StatCard) it glows from the bottom right; at the bottom (bottom bars, form footers, full-width pay buttons) from the top right; in between from the right edge. `gt-lit--top`, `--middle` and `--bottom` override the guess. Never a top-to-bottom linear gradient. The glow is painted from the border edge (`background-origin: border-box`, transparent border) so no seam line shows along an edge.

**Hatching.** Diagonal grey stripes (`hatch` on white) always mean *pending / target / not yet*: future days in charts, target bars, the unfinished part of a gauge, empty image slots. It is the brand's most distinctive mark — use it only with that meaning.

**Depth.** Flat. Cards have no shadow; hierarchy comes from white on grey and from the one dark-green HeroCard per page. Only floating things (dialogs, popovers, hovered offering cards) use `shadow-overlay` / `shadow-float`, over a dark-green `scrim`.

**Action hierarchy.** The lit `primary` button (flat: no drop shadow, no inner highlight, weight 500) is reserved for the one headline action at the top of a page (page head, or the last step of a flow). Lower actions — form, card, sheet and dialog submits — are flat `solid` green-700 pills; secondary actions are thin dark-green `outline` pills; destructive secondaries are `outline-danger`. Every form and sheet has an outline Back/Batal beside its submit.

**Footnotes.** Explanations, formulas and caveats sit behind a small (i) InfoTip chip next to their label and show on hover, focus or tap — never as always-on grey small print under a value.

**Charts.** Bars are fat pills with varied fills by meaning (peak dark, current light with a value tag, future hatched) — never a row of identical bars. Share-of-a-whole is a half-donut Gauge, never a doughnut or pie. Axes show short money (`Rp2 jt`), tooltips the exact amount. Revenue against cost is one combo chart: revenue as pill bars (`green-400`, the latest `green-600`), cost (HPP) as a 2px dashed `green-900` line drawn over them, and the difference (gross profit) in the tooltip. No separate loss series: a day below cost shows as the line above its bar. Value tags stay inside the plot, clamped 2px from every edge, never cut off.

**Three-step scales.** Good / fair / poor read `green-600` / `warning-ink` / `danger` (spray window: Baik, Cukup, Tidak direkomendasikan), always with the word.

**Maps.** The map is for looking: nothing but a column of 38px white rounded controls (zoom, draw, locate, layers) sits on it, plus a one-line 10px tile credit. Editing happens in the MapEditor panel under the map. Open on the farm, not on a country.

**Native widgets.** No browser-native look: date fields open the themed DatePicker, scrollbars are thin pills with no track or arrows.

**Shape.** Cards `radius-lg` (20px), tiles `radius-md` (12px), app frame `radius-xl`, dialogs 28px. Every button, field, chip, tab, avatar and chart bar is `radius-pill`. Status pills are the square-ish exception (`radius-xs`).

**Spacing.** Cards sit `space-3` apart and pad `space-5`; list rows `space-3`; nav groups `space-8`.

**Typography.** One family, Plus Jakarta Sans (400/500/600, files included). Numbers are the heroes: `display-xl` (60px) for headline values, `display` (44px) for page titles and timers, `heading-lg` (32px) for dialog and flow headlines, `title` (20px) for card titles, `body-lg`/`body` for text, `caption` for meta, `micro` for pills.

## Accessibility for layout

- Scroll containers (`gt-scroll`) keep 6px of padding (cancelled by a negative margin) so focus rings on fields inside are never clipped.
- Tables fit their card: put secondary figures on a `gt-cellsub` line under the main one instead of adding columns.

## Iconography

Thin outline icons, 1.5–1.75px stroke, round caps and joins, `currentColor`, 20–24px (Lucide-style). Utilities sit in 56px white circles; "go to" links are a ↗ arrow in a 36px circle (outlined, or solid green for the single most urgent item). Status uses dots and words, never icon-only.

## Motion

The bundle animates itself; no script is needed. Tokens: `--ease-out`, `--ease-spring`, `--dur-fast` (150ms), `--dur-base` (240ms), `--dur-enter` (640ms), `--stagger` (70ms).

- **Entrances.** Cards rise 20px and fade in, staggered by row (children of `gt-main`) and column (children of `gt-grid`). Rows, list items, legend entries and metric tiles follow their card 55ms apart. Fill mode is `backwards`, so content is never left hidden.
- **Charts.** Pill bars grow from the floor one after another, and their value tags pop in last. Segmented progress fills left to right. The Gauge and LineChart draw their strokes when the path has `pathLength="1"`; without it they just appear.
- **Signature.** Hatching keeps drifting slowly, so anything pending visibly reads as not finished yet.
- **Ambient.** Dark cards carry drifting contour rings. The timer's colons blink (wrap each one in `<i>`). The Time Tracker's stop button breathes. The featured StatCard gets a slow sheen. In Progress dot pills and the current Stepper mark pulse.
- **Feedback.**
  - Buttons press to 97%. A plus icon (`gt-ico--plus`) turns 90° on hover, and an arrow icon (`gt-ico--arrow`) nudges.
  - StatCards lift, and their ↗ fills and turns 45°. ActionItems tint green and their arrow turns.
  - Nav icons nudge on hover, icon buttons tint, avatars grow slightly, and checkboxes pop.
  - Dialogs rise in over a fading scrim.
- **Reduced motion.** `prefers-reduced-motion: reduce` switches every animation off and collapses transitions.

## Layout templates

- **Dashboard** (`DashboardPage`): a white framed app (`gt-app--framed`) holding a grey Sidebar (brand, MENU, GENERAL, PromoCard at the foot) and a shell of TopBar + main panel. Main: page head (title, subtitle, primary + outline action), four StatCards (first one featured), then a four-column grid: ChartBar analytics (2 cols), ReminderCard, Restock list; TeamList (2 cols), Gauge, TimeTracker.
- **Detail page:** MediaGallery → Tabs → "In short" MetricTiles → ScenarioCards → DataTables, with a sticky summary card on the right.
- **Record dialog** (`Dialog`): eyebrow, title, MetricTiles, GroupedBarChart, table with StatusPills, Callout, key facts, one outline action.
- **Form / checkout** (`CheckoutPage`): KeyValueList, Checkbox confirmations, DocumentRows, Callout, footer (Back · note · primary), then a ConfirmDialog.
- **Mobile flow** (`MobileFlowPage`): MobileFlowHeader, RadioCards, Stepper, BottomActionBar.

## Components

All components are CSS-only in `components/bundle.css`, classes prefixed `gt-` (wrap a page in `gt-root`). Each component folder has a live preview and usage notes. Groups: Actions, Inputs, Navigation, Data display, Charts, Feedback, Cards, Pages.

## Accessibility notes

- `ink-subtle` is ~3.0–3.3:1 on light surfaces: 18px+ only, otherwise `ink-muted`.
- `success-ink` and `warning-ink` (pill inks from the reference) fail 4.5:1; pills always carry the word. For yellow sentences use `warning-text`.
- `green-400` and `lime-400` are never text on white.
- Focus: 2px `focus` ring, 2px offset, on every interactive element.

## Assumptions

- Plus Jakarta Sans is a chosen stand-in for the reference's unnamed typeface.
- Tokens marked DERIVED (`divider`, `scrim`, `warning-text`, `highlight`, shadows) were added to cover the walkthrough's screens; the reference itself has no shadows or dividers.
- The walkthrough's black primary buttons and neutral palette were translated into GoTani green; its layouts and component behaviours were kept.
- Light theme only.
