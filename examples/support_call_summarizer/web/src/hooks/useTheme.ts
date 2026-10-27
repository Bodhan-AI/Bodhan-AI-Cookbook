import { useCallback, useEffect, useState } from "react";

// Same key and behaviour as the Bodhan.AI pages: an explicit choice is stored;
// without one the app follows the OS. index.html stamps data-theme before first
// paint so there is no flash; this hook keeps it in sync afterwards.
const STORAGE_KEY = "bodhan-theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

export type Theme = "light" | "dark";

function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function darkQuery(): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia(DARK_QUERY) : null;
}

function systemTheme(): Theme {
  return darkQuery()?.matches ? "dark" : "light";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? systemTheme());

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    const mq = darkQuery();
    const followSystem = () => {
      if (!storedTheme()) setTheme(systemTheme());
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setTheme(storedTheme() ?? systemTheme());
    };
    mq?.addEventListener?.("change", followSystem);
    window.addEventListener("storage", onStorage);
    return () => {
      mq?.removeEventListener?.("change", followSystem);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // storage blocked: the choice lasts for this page only
      }
      return next;
    });
  }, []);

  return { theme, toggle };
}
