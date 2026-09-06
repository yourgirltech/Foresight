/** @type {import('tailwindcss').Config} */

// Wrap a token so Tailwind opacity modifiers (bg-surface/60) still work.
const token = (name) => `rgb(var(${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Fraunces = marketing headline accent ONLY (apply `font-display`).
      // Everything else keeps Tailwind's default `font-sans` system stack —
      // body copy, nav, buttons, and the whole authenticated app.
      fontFamily: {
        display: ['"Fraunces"', "ui-serif", "Georgia", "Cambria", "serif"],
        // Auth screens only (login / signup) — a warm grotesque that pairs
        // with Fraunces. Applied via `font-grotesk` on the AuthCard root, not
        // globally: the rest of the app keeps the default `font-sans` stack.
        grotesk: [
          '"Hanken Grotesk"',
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },

      // EXTEND, never replace — replacing theme.colors wipes Tailwind's default
      // palette (slate/red/green/…), which broke an earlier project.
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#d9e6ff",
          500: "#2f6bff",
          600: "#1e54e6",
          700: "#1a44b8",
        },

        // --- theme tokens (defined per [data-theme] in src/theme/tokens.css) ---
        background: token("--color-background"),
        surface: {
          DEFAULT: token("--color-surface"),
          muted: token("--color-surface-muted"),
        },
        "text-primary": token("--color-text-primary"),
        "text-secondary": token("--color-text-secondary"),
        "text-muted": token("--color-text-muted"),
        border: {
          DEFAULT: token("--color-border"),
          strong: token("--color-border-strong"),
        },
        accent: {
          DEFAULT: token("--color-accent"),
          strong: token("--color-accent-strong"),
          soft: token("--color-accent-soft"),
        },
        success: {
          DEFAULT: token("--color-success"),
          soft: token("--color-success-soft"),
        },
        warning: {
          DEFAULT: token("--color-warning"),
          soft: token("--color-warning-soft"),
        },
        danger: {
          DEFAULT: token("--color-danger"),
          soft: token("--color-danger-soft"),
        },
        pro: {
          DEFAULT: token("--color-pro"),
          soft: token("--color-pro-soft"),
        },
      },
    },
  },
  plugins: [],
};
