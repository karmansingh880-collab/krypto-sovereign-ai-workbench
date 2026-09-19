import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeChoice = "system" | "light" | "dark";

interface ThemeValue {
  theme: ThemeChoice;
  setTheme: (theme: ThemeChoice) => void;
  cycle: () => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);
const KEY = "krypto-theme";
const ORDER: ThemeChoice[] = ["system", "light", "dark"];

function readSaved(): ThemeChoice {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system"; // storage blocked (private window etc.)
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(readSaved);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      /* not persisted */
    }
  }, [theme]);

  const setTheme = useCallback((next: ThemeChoice) => setThemeState(next), []);
  const cycle = useCallback(
    () => setThemeState((current) => ORDER[(ORDER.indexOf(current) + 1) % ORDER.length]),
    [],
  );
  const value = useMemo(() => ({ theme, setTheme, cycle }), [theme, setTheme, cycle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
