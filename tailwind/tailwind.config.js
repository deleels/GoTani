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
      },
      borderRadius: { xs: "var(--radius-xs)", tile: "var(--radius-md)", card: "var(--radius-lg)",
                      frame: "var(--radius-xl)", pill: "var(--radius-pill)" },
      fontFamily: { sans: ["var(--font-sans)"] },
      fontWeight: { bold: "600" },
    },
  },
};
