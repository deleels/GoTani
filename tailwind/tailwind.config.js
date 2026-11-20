/* Prebuilt Tailwind (D112). Same config the Play CDN used; rebuild after adding utility classes:
     npx tailwindcss@3.4.16 -c tailwind/tailwind.config.js -i tailwind/input.css -o core/static/core/tailwind.css --minify
   core.test_tailwind fails when a template uses a class the built file lacks. */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./*/templates/**/*.html",
    "./core/static/core/*.js",
    "./{core,farm,geo,inventory,invoicing,pos,reports}/**/*.py",
    "!./**/migrations/**",
    "./*/static/**/*.js",
    "!./core/static/core/ds/**",
  ],
  /* Utilities become `html .x` (0,1,1) so they tie with, and (loaded later) win over, the design
     system's `.gt-root p/h1-h3 { margin: 0 }` reset, which would otherwise zero every mt-* and mb-* margin. */
  important: "html",
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)", surface: "var(--surface)", raised: "var(--surface-raised)",
        line: "var(--divider)", "line-strong": "var(--line-strong)",
        ink: { DEFAULT: "var(--ink)", muted: "var(--ink-muted)", subtle: "var(--ink-subtle)", inverse: "var(--ink-inverse)" },
        brand: { 900: "var(--green-900)", 800: "var(--green-800)", 700: "var(--green-700)",
                 600: "var(--green-600)", 400: "var(--green-400)", 100: "var(--green-100)" },
        lime: { 400: "var(--lime-400)" },
        danger: "var(--danger)", "warning-text": "var(--warning-text)", highlight: "var(--highlight)",
        /* Temporary alias layer (remove in Phase 6 once no template uses stone-* or emerald-*). */
        stone: { 50: "#f7f7f7", 100: "#eef0ef", 200: "#e9ebea", 300: "#d4d8d6", 400: "#8c918f",
                 500: "#737373", 600: "#5c605e", 700: "#3a3d3c", 800: "#000000", 900: "#000000" },
        emerald: { 50: "#edf6ee", 100: "#edf6ee", 200: "#cfe6d7", 500: "#5fbd92",
                   600: "#227d53", 700: "#175738", 800: "#13452d" },
      },
      borderRadius: { xs: "var(--radius-xs)", tile: "var(--radius-md)", card: "var(--radius-lg)",
                      frame: "var(--radius-xl)", pill: "var(--radius-pill)" },
      fontFamily: { sans: ["var(--font-sans)"] },
      fontWeight: { bold: "600" },
    },
  },
};
