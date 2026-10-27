import { useTheme } from "../hooks/useTheme";
import { BrandLockup } from "./BrandLockup";

const API_REFERENCE = "https://console.bodhan.ai/api-docs/";
const COOKBOOK = "https://github.com/Bodhan-AI/Bodhan-AI-Cookbook";

export function AppHeader() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";

  return (
    <header className="app-header">
      <div className="app-header__inner">
        <a className="app-header__home" href="https://console.bodhan.ai/" aria-label="Bodhan.AI console">
          <BrandLockup />
        </a>
        <span className="app-header__tag">Cookbook</span>
        <nav className="app-header__links" aria-label="Bodhan.AI resources">
          <a href={API_REFERENCE}>API reference</a>
          <a href={COOKBOOK}>GitHub</a>
        </nav>
        <div className="app-header__actions">
          {/* The icon names the theme a press will give, as on the Bodhan pages. */}
          <button type="button" className="theme-toggle" aria-label={`Switch to ${next} theme`} onClick={toggle}>
            {next === "dark" ? (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </header>
  );
}
