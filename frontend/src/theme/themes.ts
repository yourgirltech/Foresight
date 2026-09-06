/**
 * The theme registry — the single list every part of the UI reads from.
 *
 * To add a theme:
 *   1. add a  [data-theme="yourname"] { ... }  block in theme/tokens.css
 *   2. add one entry here
 * Nothing else changes — the picker, persistence, and <html data-theme> wiring
 * are all driven off this array.
 */
export interface ThemeDef {
  /** value written to <html data-theme="..."> and localStorage */
  name: string;
  /** shown in the Settings picker */
  label: string;
  /** one-line description for the picker */
  description: string;
  /** which OS media query this theme is the natural default for, if any */
  prefers?: "light" | "dark";
}

export const THEMES: ThemeDef[] = [
  {
    name: "light",
    label: "Light",
    description: "Clean and bright. The default for daytime work.",
    prefers: "light",
  },
  {
    name: "dark",
    label: "Dark",
    description: "Low-light friendly. Easier on the eyes at night.",
    prefers: "dark",
  },
];

export const THEME_NAMES = THEMES.map((t) => t.name);
export const DEFAULT_THEME = "light";
export const STORAGE_KEY = "foresight:theme";

export function isThemeName(value: unknown): value is string {
  return typeof value === "string" && THEME_NAMES.includes(value);
}

/** First visit: honour the OS light/dark preference. */
export function osPreferredTheme(): string {
  if (typeof window !== "undefined" && window.matchMedia) {
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const match = THEMES.find((t) => t.prefers === (dark ? "dark" : "light"));
    if (match) return match.name;
  }
  return DEFAULT_THEME;
}

export function resolveInitialTheme(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isThemeName(stored)) return stored;
  } catch {
    /* localStorage unavailable — fall through */
  }
  return osPreferredTheme();
}
