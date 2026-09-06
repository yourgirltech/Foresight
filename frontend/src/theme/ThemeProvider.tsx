import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  DEFAULT_THEME,
  STORAGE_KEY,
  THEMES,
  isThemeName,
  osPreferredTheme,
  resolveInitialTheme,
  type ThemeDef,
} from "./themes";

interface ThemeContextValue {
  theme: string;
  themes: ThemeDef[];
  setTheme: (name: string) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

function applyTheme(name: string) {
  document.documentElement.setAttribute("data-theme", name);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<string>(() => resolveInitialTheme());

  // keep <html data-theme> in sync (an inline script in index.html sets it
  // pre-hydration to avoid a flash; this is the authoritative sync afterwards)
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // if the user has NOT made an explicit choice, follow OS changes live
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      try {
        if (!isThemeName(localStorage.getItem(STORAGE_KEY))) {
          setThemeState(osPreferredTheme());
        }
      } catch {
        setThemeState(osPreferredTheme());
      }
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((name: string) => {
    const next = isThemeName(name) ? name : DEFAULT_THEME;
    setThemeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* persistence best-effort */
    }
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, themes: THEMES, setTheme }),
    [theme, setTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within <ThemeProvider>");
  return ctx;
}
