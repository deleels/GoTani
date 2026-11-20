# GoTani redesign onto GoTani-design-system

A phase-by-phase spec for moving the GoTani app's UI onto **GoTani-design-system**. It is written for
Claude to execute one phase at a time. Each phase lists what to change, which files, and how to tell
it is done. Domain logic, URLs, form field names, Alpine state and copy stay as they are unless a
phase says otherwise.

- **Design system source:** `C:\Users\derry\Documents\UI Inspo\GoTani inspo\GoTani-design-system`
  (`README.md` holds the visual rules; `components/<Name>/README.md` and `preview.html` hold each
  component's classes and markup).
- **App:** this repository (Django templates + Tailwind Play CDN + Alpine.js + Chart.js + Leaflet).

---

## 1. Decisions (settled before work starts)

| Topic | Decision |
|---|---|
| Dark mode | **Dropped.** Light theme only. Toggle, `.dark` CSS, theme script and theme events are removed. |
| Migration strategy | **Hybrid.** Design tokens are mapped into `tailwind.config` so existing utilities pick up the new values at once; shared pieces (shell, cards, pills, tabs, dialogs, tables) move to the design system's `gt-` classes; pages are then converted group by group. Tailwind stays. |
| Status colours | Design system pills (green, yellow, red) **plus two new token pairs**: blue (`info`) and teal (`partial`), in the same soft style. |
| Money format | **`Rp15.000`** (no space) everywhere, including receipts, invoices, messages, JS and tests. |
| Shell | **Framed design-system shell** (`gt-app gt-app--framed`): grey sidebar and a grey main panel inside a white frame. The Ctrl/⌘+B collapse (to an icon rail) and the mobile slide-in sheet stay. |
| Charts | **Chart.js stays**, restyled from tokens: pill-shaped bars, token colours, hatching for targets and pending values. CSS `ChartBar`/`Gauge` only for small fixed widgets. |
| Motion | **Calm by default:** hover, press and focus feedback everywhere; entrance and ambient animations **only on Ringkasan and Analitik**. Tables, forms and the POS appear instantly. |
| Print | **Invoice print and A4 receipt fully restyled**; the 58 mm thermal receipt stays plain (font and money format only). |
| Logo | The **FarmProfile logo when uploaded**, otherwise the design system's GoTani mark and lockup. PWA icons regenerated from the design system's app icon. |
| POS | **Product card grid** (name, grade, price/kg, stock, −/+ step) with the cart on the right; on phones the cart becomes a bottom action bar. |
| Shipping | One branch, **one commit per phase**, `python manage.py test` green at every commit. |
| Copy | Indonesian copy is **not rewritten** in this project (apart from the money format). |

Record these in `docs/DECISIONS.md` in Phase 0 (section 9 has the text).

---

## 2. Ground rules for every phase

1. **Keep behaviour.** Do not rename URLs, form field `name`s, Alpine component names or `data-*`
   hooks (`data-num`, `data-confirm*`, `data-total`, `x-data` functions). Tests and scripts depend on them.
2. **Class naming.**
   - `gt-*`: design-system classes, used exactly as the component READMEs describe. Never edit
     `bundle.css` or `tokens.css` in the app; override in `gotani.css` instead.
   - `app-*`: anything the design system does not have (form fields, right-side sheet, POS product
     tile, extra pill tones, compact page head). These live in `core/static/core/gotani.css` under an
     `/* App extensions */` heading and use tokens only, never raw hex.
3. **Don't mix colour utilities on `gt-` elements.** The Tailwind CDN injects its stylesheet after the
   page's own, so a `bg-*`/`text-*` utility on a `gt-` element can override it. Use Tailwind for
   layout (`flex`, `grid`, `gap-*`, `md:*`) and the design system for look.
4. **Tokens, never hex.** New CSS references `var(--…)`. New Tailwind classes use the semantic names
   from Phase 1 (`bg-surface`, `text-ink-muted`, `rounded-card`), not `stone-*`/`emerald-*`.
5. **Design-system rules that apply here** (from its `README.md`): flat cards with no shadow (shadows only on
   dialogs, menus and sheets); one dark-green hero card per page at most; pill-shaped buttons, fields,
   chips and tabs; status pills square-ish (`radius-xs`) and always carrying the word; hatching
   only means pending, target or not yet; `ink-subtle` only at 18px+ (use `ink-muted` below);
   focus ring 2px `--focus` with a 2px offset.
6. **Every phase ends with the checks in section 8** and its own commit.

---

## 3. Phase 0: branch, decisions, inventory

1. Finish or merge the current branch (`phase-12-pkp-pos`), then create `redesign/design-system`
   from the up-to-date main branch.
2. Add D104–D106 to `docs/DECISIONS.md` (section 9).
3. Update `CLAUDE.md`'s **UI** section:
   - Brand line → "GoTani-design-system (tokens + CSS components, `gt-` classes), light theme only."
   - Remove the theme toggle from the top-bar line.
   - Status pills → "pending = yellow, issued/approved = blue (info), partially paid = teal (partial),
     overdue/void/rejected = red, paid/completed = green, draft = neutral".
   - Units & Money → "Format as `Rp15.000`."
4. Take "before" screenshots of the main screens at 1440 and 390 wide (section 8 has the list) into
   `docs/redesign-shots/before/` (keep them out of git if they are large; add the folder to `.gitignore`).

**Commit:** `docs: redesign decisions D104-D106 and spec`

---

## 4. Phase 1: foundation (tokens, fonts, Tailwind mapping, drop dark mode, money format)

### 4.1 Bring the design system in

Copy, unchanged, from the design-system folder:

| From | To |
|---|---|
| `tokens.css` | `core/static/core/ds/tokens.css` |
| `components/bundle.css` | `core/static/core/ds/bundle.css` |
| `fonts/*.woff2`, `fonts/OFL.txt` | `core/static/core/ds/fonts/` (the font URLs in `tokens.css` are relative to it) |
| `assets/Logos/*.svg` | `core/static/core/ds/logos/` |

Add `core/static/core/ds/README.md` with one line: the source folder and the date copied. Re-copy
when the design system changes; never hand-edit these files.

### 4.2 Add the app's extra tokens

At the top of `gotani.css`, in a `:root` block (light only):

```css
:root {
  /* App tokens (D104): status tones the design system lacks. Check >= 4.5:1 ink on bg. */
  --info-bg: #eaf1fa;    --info-ink: #2c5a88;     /* issued, approved, unpaid */
  --partial-bg: #e5f4f1; --partial-ink: #1d6a5e;  /* partially paid */
  --neutral-bg: var(--surface); --neutral-ink: var(--ink-muted); /* draft */
  --line-strong: #d4d8d6; /* DERIVED: field borders */
}
```

Verify both ink/bg pairs reach 4.5:1 with a contrast checker and adjust the ink only if they don't.

### 4.3 `templates/base_bare.html`

- **Remove:** the theme-before-paint `<script>`, the Google Fonts `preconnect` and stylesheet links
  (the fonts are now local), and `darkMode: "class"`.
- **Load order:** `ds/tokens.css` → `ds/bundle.css` → Tailwind CDN + config → `gotani.css`.
- `<meta name="theme-color" content="#175738">` (green-700).
- `<body class="gt-root min-h-screen bg-surface text-ink antialiased" data-motion="{% block motion %}calm{% endblock %}">`.
- Restyle the "Versi baru tersedia" banner: `bg-brand-900 text-ink-inverse rounded-card`, with the
  button as `gt-btn gt-btn--on-dark gt-btn--sm`.
- **Tailwind config** (replaces the current one-liner):

```js
tailwind.config = {
  theme: {
    extend: {
      colors: {
        /* Semantic names: use these in new and converted markup. */
        canvas: "var(--canvas)", surface: "var(--surface)", raised: "var(--surface-raised)",
        line: "var(--divider)", "line-strong": "var(--line-strong)",
        ink: { DEFAULT: "var(--ink)", muted: "var(--ink-muted)", subtle: "var(--ink-subtle)", inverse: "var(--ink-inverse)" },
        brand: { 900: "var(--green-900)", 800: "var(--green-800)", 700: "var(--green-700)",
                 600: "var(--green-600)", 400: "var(--green-400)", 100: "var(--green-100)" },
        lime: { 400: "var(--lime-400)" },
        danger: "var(--danger)", "warning-text": "var(--warning-text)", highlight: "var(--highlight)",
        /* Temporary alias layer: shifts every unconverted page onto the new palette.
           Remove in Phase 6 once no template uses stone-*/emerald-*. */
        stone: { 50: "#f7f7f7", 100: "#eef0ef", 200: "#e9ebea", 300: "#d4d8d6", 400: "#8c918f",
                 500: "#737373", 600: "#5c605e", 700: "#3a3d3c", 800: "#000000", 900: "#000000" },
        emerald: { 50: "#edf6ee", 100: "#edf6ee", 200: "#cfe6d7", 500: "#5fbd92",
                   600: "#227d53", 700: "#175738", 800: "#13452d" },
      },
      borderRadius: { xs: "var(--radius-xs)", tile: "var(--radius-md)", card: "var(--radius-lg)",
                      frame: "var(--radius-xl)", pill: "var(--radius-pill)" },
      fontFamily: { sans: ["var(--font-sans)"] },
      fontWeight: { bold: "600" }, /* only 400/500/600 files ship */
    },
  },
};
```

Note: colours given as `var(...)` don't support Tailwind opacity modifiers (`bg-brand-700/50`). Where a
translucent colour is needed, write it in `gotani.css`.

### 4.4 Drop dark mode

| File | Change |
|---|---|
| `templates/base.html` | Remove the theme button, the `dark` state, `toggleTheme()` and the `gotani-theme` event. Keep `collapsed`, `mobile`, `toggle()`, `save()`, `columns()`. |
| `core/static/core/gotani.css` | Delete every `.dark …` rule and the `.dark .viz-root` rule. Keep `[x-cloak]`, the print rules, `#sw-update[hidden]`. |
| `templates/core/home.html` | Replace the `ink()`/`grid()` dark checks with token reads (Phase 4 restyles the chart fully); remove the `gotani-theme` listener. |
| `templates/reports/dashboard.html` | Remove the `gotani-theme` listener and the chart re-colouring it triggers. |
| `templates/core/offline.html` | Remove `color-scheme: light dark` and the `prefers-color-scheme: dark` block; set its `--brand` to `#175738` and `--bg` to `#f7f7f7`. |
| `core/templatetags/ui.py` | Delete the `sun` and `moon` icons once nothing uses them. |
| `core/static/core/pwa.js` (or `base.html`) | One-time cleanup: `try { localStorage.removeItem("gotani-theme") } catch (e) {}`. |

Afterwards, `grep -rn "dark" templates core/static/core --include=*.html --include=*.js --include=*.css`
must only find unrelated words (the design system's `.gt-dark` card class, `--on-dark` modifiers).

### 4.5 Money format `Rp15.000`

**Python:**

| Location | Change |
|---|---|
| `farm/templatetags/gotani.py:30` (`rupiah` filter) | `f"{sign}Rp{…}"` |
| `pos/services.py:295` | `"Rp" + …` |
| `core/notifications.py:79` | `f"… sisa Rp{outstanding:,.0f}"` |
| `invoicing/views.py:368` | `f" Sisa Rp{left:,.0f} …"` |
| `farm/models.py:482` (`__str__`) | `Rp{self.amount_idr}` |
| `pos/services.py:415` | Error message: `Rp{denom}` |

**JS:** add one helper in `core/static/core/numbers.js`:
`window.gtRupiah = (v) => "Rp" + Math.round(Number(v || 0)).toLocaleString("id-ID");`. Replace the
local `rp()` functions and inline `'Rp ' + …` in `warnings.js`, `stage.js`,
`invoicing/invoice_editor.html`, `invoicing/payment_form.html`, `pos/register.html`,
`pos/shift.html` (including the static `Rp 0`) and `reports/dashboard.html`.

**Parsing:** `core.numbers` already accepts `Rp 15.000,5`. Add `Rp15.000` and `Rp15.000,5` cases to
`core/test_numbers.py` so pasting either form still parses.

**Tests to update:** `reports/tests.py` lines 212, 246, 402, 604, 621, 622 (`"Rp 10.000"` →
`"Rp10.000"`, …). `farm/test_p9.py` lines 151, 185, 189, 283 and `reports/tests.py` 192, 208 check
`"Rp "` to prove money is hidden or shown by role. Change them to a regex on `Rp\d` (for example
`assertNotRegex(r.content.decode(), r"Rp\d")`) so the check stays exact after the space is gone.

Finally `grep -rn "Rp " --include=*.py --include=*.html --include=*.js .` (excluding `.venv`) must
return nothing but comments and `Rp {{` template text that you then fix.

### 4.6 Phase 1 done when

- All pages render in the new palette and font through the alias layer, with no dark mode anywhere.
- Every amount reads `Rp15.000`.
- Tests are green.

**Commit:** `ui: design-system tokens and fonts, Tailwind mapping, drop dark mode, Rp15.000`

---

## 5. Phase 2: shell (frame, sidebar, top bar, login, logo, motion switch)

### 5.1 Frame (`templates/base.html`)

Target structure (from `components/DashboardPage/preview.html`):

```html
<div x-data="shell()" … class="gt-app gt-app--framed app-frame" :class="collapsed && 'app-frame--rail'">
  <aside class="gt-sidebar …">            <!-- brand, nav groups, foot -->
  <div class="gt-shell">
    <header class="gt-topbar …">           <!-- toggle, spacer, bell, user -->
    <main class="gt-main print-full">      <!-- subnav, messages, content -->
  </div>
</div>
```

- `app-frame`: `min-height: 100vh`. On screens ≥ md, outer padding `var(--space-3)` on `canvas` per the
  design system. Below md: no frame padding, no outer radius, the sidebar becomes the slide-in sheet
  (existing `mobile` state; panel `var(--surface)`, scrim `var(--scrim)`).
- `gt-main` keeps `max-width: 80rem` content inside via an inner wrapper, padding `var(--space-5)`;
  its scrolling stays on the page (no inner scroll container), so print and sticky elements behave
  as before.

### 5.2 Sidebar

- **Brand:** `gt-brand`. Show `farm_profile.logo` (40px, `radius-md`) when set, else
  `ds/logos/gotani-mark.svg`. The farm name sits next to it and the role on a second line in
  `ink-muted` 14px.
- **Groups:** `gt-nav` with `gt-nav__label` per group (DASBOR, PENJUALAN, KEUANGAN, ALAT). Items are
  `gt-nav__item` with `aria-current="page"` on the active one (it draws the green bar).
- **Children** (Aset Kebun ▾): the parent is a `gt-nav__item` button with the chevron. Children are
  `gt-nav__item app-nav__child` (indented `space-8`, 16px text, no icon).
- **Foot** (`gt-sidebar__foot`): a `PromoCard` "Pasang GoTani" install card shown only when the
  browser fired `beforeinstallprompt` (wire it in `pwa.js`; hide it once installed). Otherwise the
  foot stays empty. Remove the "Ctrl/⌘ + B untuk menciutkan" line and put the hint in the toggle's
  `title`.
- **Collapsed** (`collapsed`, Ctrl/⌘+B, saved in localStorage as now): the sidebar renders as an
  80px `gt-rail`, with the app icon on top, each item as a `gt-rail__item` (`--active` on the current
  area) carrying `title` and `aria-label`, and group labels hidden. Clicking a parent with children
  expands the sidebar, as now.

### 5.3 Top bar

`gt-topbar` on `surface`, never in a card. Left: the sidebar toggle as `gt-iconbtn gt-iconbtn--md`
(desktop) and the menu button (mobile), each with an `aria-label`. Right: the bell as
`gt-iconbtn gt-iconbtn--md` with `<span class="gt-count">` for unread, then `gt-user` (avatar initial,
name, role) opening the existing menu. Drop the dashed divider.

**Bell and user menus:** `raised` background, `radius-lg`, `shadow-float`, rows as `gt-row` (title +
meta), unread rows with a `gt-dot` instead of the green tint. Logic unchanged.

### 5.4 Motion switch

`<body data-motion="calm">` is the default from Phase 1. `core/home.html` and `reports/dashboard.html`
add `{% block motion %}full{% endblock %}`. In `gotani.css`:

```css
/* D106: calm pages keep hover/press/focus transitions and dialog/checkbox feedback only. */
.gt-root[data-motion="calm"] :is(.gt-rail,.gt-sidebar,.gt-topbar,.gt-pagehead,.gt-stat,.gt-card,.gt-dark,
  .gt-hero,.gt-offer,.gt-radio,.gt-scenario,.gt-gallery,.gt-stat__value,.gt-hero__value strong,.gt-dark__timer,
  .gt-bar__fill,.gt-gbar__b,.gt-bar__val,.gt-bar__tip,.gt-seg i,.gt-media--hatch,.gt-legend i,.gt-gauge path,
  .gt-gauge__value,.gt-line__path,.gt-line__pt,.gt-line__end,.gt-line__tip,.gt-line__halo,.gt-count,.gt-nav__badge,
  .gt-badge,.gt-step__mark,.gt-pill),
.gt-root[data-motion="calm"] :is(.gt-list,.gt-feed,.gt-nav,.gt-stepper,.gt-kv,.gt-metrics,.gt-hero__stats,.gt-legend)>*,
.gt-root[data-motion="calm"] .gt-card>.gt-action,
.gt-root[data-motion="calm"] :is(.gt-dark,.gt-stat--featured,.gt-nav__item,.gt-dark__controls .gt-iconbtn--danger)::before,
.gt-root[data-motion="calm"] :is(.gt-dark,.gt-stat--featured)::after { animation: none; }
```

Also kill the sidebar's entrance on full pages (it would replay on every navigation):
`.gt-sidebar, .gt-topbar { animation: none; }`.

### 5.5 Login (`templates/registration/login.html`)

Keep the split screen and copy. The left panel is a design-system dark card: `var(--green-900)`, the
`gotani-lockup-white.svg` logo, the headline at `heading-lg`/`display`, the tagline in white at 80%.
Remove the blurred blob. The right panel is `raised`. Fields use `app-field` (Phase 3). The password
eye toggle is a `gt-iconbtn gt-iconbtn--sm gt-iconbtn--sunken` inside the field. "Masuk" is
`gt-btn gt-btn--primary gt-btn--md gt-btn--block`.

### 5.6 PWA icons and manifest

- `core/pwa.py`: `theme_color "#175738"`, `background_color "#f7f7f7"`.
- Regenerate `core/static/core/icons/*.png` (favicon-32, apple-touch, 192, 512, maskable 192/512 with
  a 10% safe zone) from `ds/logos/gotani-app-icon.svg`. Render with headless Edge/Chrome
  (`--headless --screenshot --window-size=N,N` on a tiny HTML page holding the SVG) and commit the
  PNGs. Add no new Python dependency.

**Commit:** `ui: design-system shell, sidebar rail, top bar, login, icons`

---

## 6. Phase 3: shared parts (restyle every page through the includes)

| Piece | File(s) | New markup |
|---|---|---|
| Page head | `components/page_header.html` | `gt-pagehead app-pagehead--sm` (h1 at `heading-lg` 32px, subtitle `ink-muted`). Add an optional `actions` template variable rendered in `gt-pagehead__actions`, so pages can pass rendered buttons; pages that keep their own action block are fixed in Phase 4/5. Ringkasan and Analitik use the full 44px `gt-pagehead`. |
| KPI | `components/kpi.html` | Default: `gt-metric gt-metric--raised` (label, value, note). With `stat=True`: `gt-stat` (+ `featured=True` → `gt-stat--featured`, + `href` → `a.gt-stat` with the ↗ arrow). The `cls` parameter maps to tones (`text-red-*` → `app-value--danger`, `text-emerald-*` → none). |
| Status pill | `core/templatetags/ui.py` `pill` | Output `<span class="gt-pill gt-pill--{tone} app-pill">`. Tone map: yellow → `progress`, red → `pending`, green → `completed`, blue → `app-pill--info`, teal → `app-pill--partial`, gray → `app-pill--neutral`. `STATUS_TONES` stays. `.app-pill` sets the size to 12px/22px high: the design system's 10px is too small for dense tables. Add `--dot` for live states (In Progress, Overdue). |
| Tabs | `components/tabs.html` | `<nav class="gt-tabs" aria-label="…">` with `<a class="gt-tab">`, plus `gt-tab--active` and `aria-current="page"` on the current one (these are links, so no `role="tab"`). |
| Range switch | wherever a Week/Month/Year control exists | `gt-tabs gt-tabs--boxed`. |
| Messages | `components/messages.html` | `gt-callout` with an icon; `success` → `--success`, `warning` → `--warning`, `error` → `--danger`, default neutral. The message is the `gt-callout__title`. |
| Pagination | `components/pagination.html` | Row inside the table card's `gt-card__foot`; `per` select as `app-field app-field--sm`; « ‹ › » as `gt-iconbtn gt-iconbtn--sm gt-iconbtn--outline` with the same `aria-label`s, disabled ones as `<span aria-disabled="true">` at 40% opacity. |
| Column menu | `components/columns.html` | Trigger `gt-btn gt-btn--outline gt-btn--sm`; panel `raised` + `radius-lg` + `shadow-float`; options as `gt-check`. |
| Bell | `components/bell.html` | Done in Phase 2. |
| Tables | every `<table>` (converted per page in Phases 4–5; the base style lands now) | `gt-table` inside a `gt-card` (no outer border, hairline rows, tabular figures right-aligned). The row ⋯ menu stays an icon button (`gt-iconbtn--sm`). A selected or base row may use `gt-table__hl`. |
| Form fields | `gotani.css` global `input/select/textarea` rule + `templates/_form_fields.html` + `farm/form_page.html` | `.app-field`: 44px high, `radius-pill` (textarea `radius-md`), 1.5px `--line-strong` border, `raised` background, 16px text, focus = `--focus` 2px ring with 2px offset. Labels 14px `ink` 500 above the field; help text 14px `ink-muted` tied with `aria-describedby`; errors 14px `var(--danger)` under the field. Checkboxes → `gt-check`; radio groups of 2–4 → `gt-radio` cards. |
| Buttons | every `<button>`/`<a>` styled as a button | Main action → `gt-btn gt-btn--primary` (`--md` in forms and dialogs, `--sm` in cards); secondary → `--outline`; destructive → `--danger`; top-bar utility → `--ghost`. One primary per view. A plus icon gets `gt-ico gt-ico--plus`, an arrow `gt-ico--arrow`. |
| Icons | `ui.py` `icon` | Add the `gt-ico` class (1.6 stroke, round caps) next to the size; keep the signature. |
| Confirm dialog | `core/static/core/confirm.js` | Build `<dialog class="gt-dialog gt-dialog--confirm">` with `gt-dialog__icon` (alert icon in a danger tile when `danger`), `gt-dialog__title`, body, the level-2 checkbox as `gt-check`, reason as `app-field`, choices as `gt-radio`, and actions as `gt-btn--outline` "Batal" plus `gt-btn--primary` or `--danger`. `::backdrop { background: var(--scrim) }`. The data-attribute API is unchanged. |
| Right-side sheets | `core/staff.html`, `farm/activities.html`, `farm/cycle_detail.html`, `farm/element_detail.html`, `farm/input_detail.html`, `invoicing/_buyer_sheet.html` | Extract the repeated sheet into `templates/components/sheet.html` (open/close/escape/focus trap kept): `.app-sheet` panel on `raised`, 28px radius on the left corners, `shadow-overlay`, 480px wide (full width below md), head with `gt-dialog__title` and a `gt-dialog__close` button, body padding `space-6`, sticky foot with the form's buttons. The scrim is `var(--scrim)`. |
| Empty states | everywhere `_empty_row.html` and the like | `gt-callout` (neutral) with a title, one explanation line and the CTA as `gt-btn--outline gt-btn--sm`. |

**Commit:** `ui: shared components on the design system (page head, KPI, pills, tabs, tables, fields, dialogs, sheets)`

---

## 7. Phases 4–5: pages, by group

Convert each group to semantic Tailwind names plus `gt-` components, removing every `stone-*` and
`emerald-*` from its templates. One commit per group.

### Phase 4a: Ringkasan (`core/home.html`), `data-motion="full"`

DashboardPage layout:
- `gt-pagehead`: a greeting with the day and date ("Selamat pagi, {nama}" / "Jumat 13 November"),
  and "Catat Kerja Hari Ini" as the primary action.
- **Work report strip:** a row of four `gt-stat` (the week's counts per activity type, the first one
  featured).
- **`gt-grid` four columns:** "Tanaman per Tahap" Chart.js (2 cols), weather widget card, counters per
  element type as `gt-metrics`; then staff list (`gt-list`), the finance card.
- **Finance card** (OWNER/MANAGER only) is the page's one `gt-hero` (dark green): all-time sales as
  `gt-hero__value`, expenses and the general vs element split as `gt-hero__stats`. For other roles
  there is no hero.
- **Weather widget** (`geo/_weather_widget.html`): current conditions large, the 7-day forecast as a
  `gt-list`, the rain warning as `gt-callout--warning`. The "Tidak dapat memuat … · Coba lagi" state
  stays.

### Phase 4b: Analitik (`reports/dashboard.html`), `data-motion="full"`

- `gt-pagehead` with the date range form (two `app-field` dates and the existing presets as
  `gt-tabs--boxed`).
- **KPI row:** `gt-stat` (net, first and featured), then gross margin, revenue and PPN collected.
- **Charts** each in a `gt-card` with a `gt-card__head`.
- **Tables** (margin by variety × grade, top products, expenses by category) as `gt-table`.
- **Receivables:** outstanding vs overdue as `gt-metrics` with a link to Pembeli.

**Chart.js styling** (one shared helper, `core/static/core/charts.js`, used by home, dashboard and
geo map):

```js
const t = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
Chart.defaults.font.family = t("--font-sans");
Chart.defaults.color = t("--ink-muted");
Chart.defaults.borderColor = t("--divider");
// Bars: pill-shaped. Lines: 2px, no fill.
const BAR = { borderRadius: 999, borderSkipped: false, maxBarThickness: 36 };
// Series order: --green-600, --green-800, --green-400, --hatch; red (--danger) only for losses/spoilage.
// Targets, budgets and future/pending values use a diagonal hatch CanvasPattern
// (stripes of --hatch on --surface-raised), never a solid colour.
// Tooltip: background --surface-raised, title/body --ink, border --divider, radius 12, padding 12.
```

Remove `.viz-root` and its hard-coded series colours. Axis labels are 12px `ink-muted`, money ticks in
the short form (`Rp277,5 jt`, `Rp593rb`), tooltips in the exact form (`Rp277.512.000`).

### Phase 4c: Kasir and sales (`pos/*`)

**`pos/register.html` (product card grid, all Alpine logic and posted field names unchanged):**
- **Layout ≥ lg:** product grid (left, `repeat(auto-fill, minmax(220px, 1fr))`) and the cart card
  (right, sticky, 380px).
- **Product tile `.app-product`:** a `gt-card` button showing the variety name (`title`), grade as
  `gt-chip` ("Grade A" `--tint` / "Grade B" neutral), price/kg at 24px 500, and stock as `gt-chip`
  ("12,5 kg"; text in `var(--danger)` with a `gt-dot--danger` when ≤ `min_alert_kg`). Clicking adds one `pos_step_kg`.
  Hover = the design system's card lift. Products without a price stay hidden (rule 20). No images and no
  hatching (hatching means pending).
- **Search/filter** above the grid: an `app-field` with the search icon.
- **Cart:** lines as `gt-row` (name, kg × price, line total, `gt-iconbtn--sm` −/+ and a remove
  button), step chips as `gt-chip` buttons, totals as `gt-kv` ("termasuk PPN Rp…" line kept), payment
  method as three `gt-radio` cards (CASH / QRIS / TRANSFER), cash received `app-field` with change
  shown as a `gt-metric`, and `gt-btn gt-btn--primary gt-btn--block` "Bayar Rp…".
- **No open shift:** `gt-callout--warning` with "Buka shift" and the grid disabled, as now.
- **< lg:** one column; the cart becomes a `gt-bottombar` (total + "Lihat keranjang" opening the cart
  as an `app-sheet` from the bottom). Tap targets are at least 44px.
- **Error from checkout** (insufficient stock names product and kg): `gt-callout--danger` above the cart.

**Other `pos/*` pages:**
- `pos/orders.html`: the table page pattern (pagehead, KPI metrics, tabs, toolbar, `gt-table`,
  pagination). Void uses `{% void_confirm %}` (styled by Phase 3).
- `pos/shift.html`: open shift as a `gt-card` with `gt-kv` (float, expected); the blind count as a
  `gt-table` of denominations with `app-field--sm` inputs; "Total dihitung" as `gt-metric`; the
  difference as `gt-callout--warning` ("Perlu ditinjau").
- `pos/receipt.html`: see Phase 5c.

### Phase 4d: Invoicing (`invoicing/*`)

- `invoices.html`, `buyers.html`: the table page pattern; payment status pills from the new tone map.
- `invoice_editor.html` (CheckoutPage pattern):
  - Buyer and dates in a `gt-card` with `gt-kv`.
  - Lines as a `gt-table` with `app-field--sm` inputs and an "+ Tambah baris" `gt-btn--outline --sm`.
  - Discount and shipping fields; totals as `gt-kv` (subtotal, diskon, PPN, ongkir, **total**).
  - Bea meterai reminder (> Rp5.000.000) as `gt-callout--warning`.
  - Form footer: "Kembali" `--outline` · a reassurance note ("Belum ada stok yang bergerak sampai
    invoice diterbitkan.") · primary "Terbitkan invoice" (level-2 confirm, D88).
- `invoice_detail.html`:
  - Overview card with `gt-metrics` (total, dibayar, sisa, jatuh tempo).
  - `gt-tabs` (Baris, Pembayaran, Riwayat).
  - Allocations as `gt-table`, history as `gt-feed`.
- `payment_form.html`:
  - Method as `gt-radio` cards. PPh withheld shows the slip-number field.
  - Allocation `gt-table` with per-row `app-field--sm`.
  - The live "Dialokasikan … · menjadi kredit …" summary as `gt-kv`; negative is `var(--danger)`,
    positive `var(--green-700)`.
- `buyer_detail.html`: `gt-metrics` (outstanding, overdue, credit), ageing buckets as a small
  `gt-gbar` or `gt-table`, invoices and payments tables.

### Phase 5a: Farm (`farm/*`, `geo/*`)

- **List pages** (`cycles`, `activities`, `expenses`, `harvests`, `inputs`, `spoilage`, `prices`,
  `opname`): the table page pattern. "Laporan Baru" picker as a menu of `gt-row`s.
- **`cycle_detail.html`:**
  - Overview with the stage as `gt-seg gt-seg--steps` (8 stages, done/current/todo; the todo segments
    are hatched, meaning "not yet").
  - `gt-metrics` "Ringkas": days growing, expected harvest, yield estimate vs actual.
  - `gt-tabs`; the pre-harvest checklist as `gt-check` rows with `completed_pct` as `gt-seg`.
  - The timeline (`forward_trace`) as `gt-stepper`.
  - The stage form (`_stage_form.html`) in an `app-sheet`, with the "Lewati tahap" checkbox as `gt-check`.
- **`element_detail.html`, `input_detail.html`:** the same detail pattern. Low stock is shown with
  `gt-callout--warning`; ledgers as `gt-table`.
- **Harvest entry** (full page, review step D85): `gt-kv` review summary, warnings as
  `gt-callout--warning`, cost/kg as `gt-metric`.
- **`geo/map.html`:**
  - Leaflet map inside a `gt-card` (radius-lg, `overflow:hidden`).
  - Polygon list as `gt-list--divided`; the "use plotted area" action as `gt-btn--outline --sm`.
  - NDVI/EVI/NDWI as `gt-metrics`, the history chart through `charts.js`.
  - Spray hint (`_spray_hint.html`) as `gt-callout`.
  - Leaflet's own controls are untouched.

### Phase 5b: Admin and tools (`core/*`, `reports/trace_*`, `reports/omzet_pph.html`, `403.html`)

- `staff.html`: `gt-table` with `gt-person` (avatar + name + role) cells; create/edit in `app-sheet`.
- `settings.html`, `profile.html`, `password_change.html`: forms in `gt-card`s with section titles,
  `app-field`s, one primary per card.
- `audit_log.html`: `gt-table` with before/after in a `gt-kv--compact` expander.
- `trace_*`: index with a search `app-field`; results as `gt-stepper` (forward/backward chain) and
  `gt-table`; print CSS kept.
- `omzet_pph.html`: `gt-metrics` per month, `gt-table`; the "konfirmasi ke konsultan pajak" note as
  `gt-callout`.
- `403.html`, `offline.html`: a centred `gt-card` with the mark, title and one action.

### Phase 5c: Print (`invoicing/invoice_print.html`, `pos/receipt.html`)

- **Invoice print (A4), full restyle:**
  - Header: farm logo or the design-system lockup left; "INVOICE", number and dates right at `title` size.
  - Seller and buyer blocks as `gt-kv--compact`; lines as `gt-table` (header row on `surface`, hairlines).
  - Totals as `gt-kv` right-aligned, the total at 24px 600.
  - The meterai box (D102) as a 1.5px `--line-strong` box, square.
  - Footer: payment terms in `ink-muted`.
  - Rules: no animations, `print-color-adjust: exact`, `@page { size: A4; margin: 16mm }`, black ink
    for all figures, nothing under `ink-muted`.
- **Receipt, A4 variant:** the same treatment, compact.
- **Receipt, 58 mm thermal:** layout unchanged; only the font (Plus Jakarta Sans, falling back to the
  system font if the printer driver substitutes it) and the `Rp15.000` format.

---

## 8. Phase 6: clean-up and final checks

1. Remove the `stone`/`emerald` alias layer from `tailwind.config`. Then
   `grep -rnE "(stone|emerald|amber|sky|teal|red)-[0-9]{2,3}" templates core/static` must return
   nothing (status colours now come from tokens/`app-pill`; errors from `--danger`).
2. Delete dead CSS in `gotani.css` and unused icons in `ui.py`.
3. Update `CLAUDE.md` "Tech Stack" (fonts are local; the design system lives in `core/static/core/ds/`).

### Checks for every phase

```bash
python manage.py check
python manage.py test
python manage.py seed_demo && python manage.py check_mass_balance
python manage.py runserver
```

Then open these screens in the browser at **1440×900, 768×1024 and 390×844**, logged in as OWNER and
once as CASHIER:

Login · Ringkasan · Analitik · Kasir (with and without an open shift, cart with items, the error
state) · Pesanan · Shift & Kas · Invoice list · Invoice editor · Invoice detail · Payment form ·
Pembeli detail · Siklus Tanam detail · Laporan Kerja · Biaya · Cuaca & Satelit · Staf · Pengaturan ·
the confirm dialog (level 1 and 2, void with reasons) · a right-side sheet with a validation error ·
the bell menu · invoice print preview · receipt (58 mm and A4) · the offline page.

On each screen, check:
- No horizontal scroll at 390.
- Tap targets of at least 44px on phones.
- Focus ring visible when tabbing; Tab order follows reading order; Esc closes dialogs and sheets.
- Only one primary button per view, and at most one dark-green hero card.
- Pills always carry a word.
- `ink-subtle` never below 18px; body text contrast ≥ 4.5:1 (check new pairs with a contrast checker).
- Hatching appears only for pending, target or not-yet values.
- Calm pages show no entrance animation; Ringkasan and Analitik animate once; under
  `prefers-reduced-motion: reduce` nothing animates.
- Every number is `Rp15.000`.

Compare with the Phase 0 "before" shots and list in the phase's commit body anything that moved or
disappeared.

---

## 8b. Phase 7: feedback round (agreed 2026-11-20, runs before Phase 6)

Agreed with the owner in a grilling session. One commit per step, tests green each time, check-in after step 1.

1. **Language.** English becomes the source language; the current Indonesian becomes the `id` translation
   (unchanged). Per-user language (EN | ID) in Profil and the account menu; defaults OWNER/MANAGER → en,
   CASHIER/WORKER → id. Industry terms in English (Revenue, Gross profit, Net profit, COGS, Gross margin,
   AOV, LTGP, Cash flow, Install, Shift, Invoice). No machine translation; one glossary (~150 terms) reviewed
   by the owner together with 5 screens in English (Dashboard, Kasir, Invoice, Biaya, Siklus) before the rest.
   A test fails on any untranslated string. Number/date format follows the language (en: Rp3,000 · 0.5 kg ·
   31 Dec 2026; id: Rp3.000 · 0,5 kg · 31 Des 2026). Buyer documents follow a per-buyer "Document language"
   (default id); POS receipts stay id.
2. **Forms, messages, motion.** Messages become toasts (bottom right); blocking notices (forced password
   change) become a centred dialog. Browser validation bubbles replaced by inline app messages + scroll to
   the first error. One page transition everywhere (~150 ms fade, one easing/duration scale), a thin top
   progress bar, repeat clicks on a loading nav link ignored, no layout shift between pages (stable scrollbar
   gutter, fixed sidebar); nothing under reduced motion; Dashboard/Analytics keep their one-time chart
   animation. Supersedes D106. No "— pilih —" placeholder text.
3. **Page fixes.** Biaya: Setujui/Tolak/Hapus one row, same size; Elemen/Siklus hidden when scope is Umum.
   Harga produk: remove "langkah kasir" (field and DB column; POS steps 0.5 kg), merge current price and
   price/kg into one column, own header over the low-stock input, "Belum diberi harga". Shift: close-shift
   form in two columns. Elemen kebun: current stage is the headline, "Majukan tahap" a smaller button.
   Spray "Buruk" → "Tidak direkomendasikan". Login: plain left panel (three.js later). Warning icon not
   clipped. Map field list looks clickable (hover, cursor, chevron). "Pasang" → "Install".
4. **Invoice payments.** Reproduce and fix partial payment; payment opens prefilled with the balance;
   overpayment confirmed as buyer credit with an option to apply it; warnings name the invoice and amount.
   "Withholds tax" checkbox per buyer (off by default) shows the tax-slip method (with required slip number)
   only for those buyers. Discount value hidden when "no discount". Every money input shows Rp, groups
   thousands live, keeps the caret, accepts pasted "Rp 3.000"; kg inputs 3 decimals (all fields audited).
5. **Metrics (Hormozi-style).** Dashboard: Revenue, Gross profit, Gross margin %, AOV, Net profit. Buyer page:
   LTGP, orders, average days between orders, last order, "At risk" when overdue by > 2× their usual gap;
   buyers list sortable by LTGP. CAC and LTGP:CAC left out (no marketing-spend data). Every metric has an
   info chip with its meaning and formula.
6. **Map and admin.** Polygon drawing: undo last point (button + Backspace); after finishing, vertices stay
   draggable, midpoint handles add points, right-click deletes a point, saved only on "Simpan"; restyled
   shapes (green outline, soft fill, white round handles). NDVI: smooth colour ramp, labelled legend, cleaner
   timeline. Django admin restyled with the design system by overriding admin templates/CSS (no new library).
7. **Phase 6** clean-up (below) with the lighter screen check agreed: OWNER every screen at 1440 and 390,
   CASHIER on its own screens, 768 spot checks; stat-card tone via `tone=` instead of colour classes; delete
   only provably unused CSS/icons; verify `core/static/core/ds/` matches the design-system source; keep the
   Tailwind CDN. Branch is not pushed.

---

## 9. Text for `docs/DECISIONS.md`

- **D104 Adopt GoTani-design-system; light theme only.** The app takes its tokens, font and `gt-`
  components from GoTani-design-system (copied into `core/static/core/ds/`, never edited in place;
  app-only parts use `app-` classes in `gotani.css`). Tailwind stays for layout. Dark mode is removed
  (supersedes the theme part of D66/D67) because the design system defines a light theme only and
  maintaining a second palette doubles every UI change. Blue (info) and teal (partial) pill tones are
  added as app tokens to keep the D71 status mapping.
- **D105 Money is written `Rp15.000`.** No space between the symbol and the amount, everywhere
  (screens, receipts, invoices, notifications). Parsing still accepts both forms. Charts may abbreviate
  (`Rp277,5 jt`, `Rp593rb`); documents never do.
- **D106 Calm motion by default.** Work screens show only hover, press and focus feedback and dialog
  transitions; the design system's entrance and ambient animations run only on Ringkasan and Analitik.
  The reason is that staff open list, form and POS screens dozens of times a day, and replayed
  entrances slow them down. Reduced-motion users get no animation anywhere.

---

## 10. Risks and how each phase handles them

| Risk | Handling |
|---|---|
| Tailwind CDN styles override `gt-` classes | Rule 2.3: no colour utilities on `gt-` elements. Layout utilities only. |
| Tests assert text that changes | Only the money format changes text. Phase 1 updates those tests and makes the role-visibility tests regex-based. |
| Tables too dense for 60px design-system numbers | List pages use `gt-metric` KPIs and the compact page head. `gt-stat`/44px heads only on Ringkasan and Analitik. |
| 10px design-system pills unreadable in tables | `.app-pill` raises them to 12px. |
| POS regression (posting the wrong fields) | The register's Alpine component and form inputs are kept. Phase 4c is restyle plus layout only. Test a full CASH and a QRIS checkout, a void, and the shift count after the change. |
| Thermal printer layout breaks | The 58 mm template keeps its structure; only the font and money format change. Print one real receipt if a printer is available. |
| Icons from the design system's README (Lucide-style) differ from `ui.py` icons | Keep `ui.py` icons (already Lucide-style). Add only `gt-ico` for the stroke width. |
