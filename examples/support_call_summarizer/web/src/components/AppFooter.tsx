import { BrandLockup } from "./BrandLockup";

export function AppFooter() {
  return (
    <footer className="app-footer">
      <div className="app-footer__inner">
        <BrandLockup />
        <p className="app-footer__line">
          An example from the <a href="https://github.com/Bodhan-AI/Bodhan-AI-Cookbook">Bodhan AI Cookbook</a>.
          Speech-to-Text and Translation by <a href="https://console.bodhan.ai/">Bodhan.AI</a>. Recordings are deleted
          after processing.
        </p>
      </div>
    </footer>
  );
}
