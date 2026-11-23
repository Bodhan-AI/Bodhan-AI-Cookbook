import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { Menu, X, LogIn, LayoutDashboard } from "lucide-react";
import { useAuth } from "../lib/auth";
import { useMode } from "../lib/mode";

const NAV = [
  ["/explore", "Explore"], ["/preserve", "Preserve"], ["/digitizer", "Digitizer"], ["/scripts", "Scripts"],
  ["/audio", "Audio"], ["/map", "Map"], ["/timeline", "Timeline"], ["/future", "Future"], ["/try", "Try it"],
] as const;

export const Logo = () => (
  <Link to="/" className="flex items-center gap-2.5" aria-label="HeritageLens home">
    <svg width="32" height="32" viewBox="0 0 64 64" aria-hidden><rect width="64" height="64" rx="14" fill="#985541" /><circle cx="32" cy="32" r="14" fill="none" stroke="#FAF7F2" strokeWidth="4" /><circle cx="32" cy="32" r="5" fill="#E8CFC0" /></svg>
    <span className="font-serif text-xl font-semibold tracking-tight">HeritageLens</span>
  </Link>
);

function ModeSwitch() {
  const { mode, setMode } = useMode();
  return (
    <div role="group" aria-label="Data mode" className="inline-flex rounded-full border border-clay-muted bg-white p-0.5 text-xs font-semibold">
      {(["demo", "live"] as const).map((m) => (
        <button key={m} onClick={() => setMode(m)} aria-pressed={mode === m}
          className={`rounded-full px-3 py-1 uppercase tracking-wide transition ${mode === m ? "bg-clay text-white" : "text-clay-deep hover:bg-clay-pale"}`}>{m}</button>
      ))}
    </div>
  );
}

function ModeBanner() {
  const { mode, status, statusError } = useMode();
  const n = status ? Object.values(status.services).filter((s) => s.configured).length : null;
  return (
    <div role="status" data-testid="mode-banner" className={`px-4 py-1.5 text-center text-xs font-semibold ${mode === "demo" ? "bg-clay-pale text-clay-deep" : "bg-clay-deep text-white"}`}>
      {mode === "demo"
        ? "DEMO MODE — sample records are labelled DEMO. Bodhan AI is not called and no credits are used."
        : `LIVE MODE — actions call real Bodhan services through the server. ${statusError ? "Server status unavailable." : n === null ? "" : `${n} of 5 services have a key configured.`}`}
    </div>
  );
}

export default function Layout() {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();
  const loc = useLocation();
  const link = ({ isActive }: { isActive: boolean }) => `rounded-full px-3 py-1.5 text-sm font-medium transition ${isActive ? "bg-clay-pale" : "hover:bg-clay-pale/60"}`;
  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-white focus:px-4 focus:py-2">Skip to content</a>
      <ModeBanner />
      <header className="sticky top-0 z-30 border-b border-line bg-ivory/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Logo />
          <nav aria-label="Main" className="hidden items-center gap-0.5 xl:flex">{NAV.map(([to, label]) => <NavLink key={to} to={to} className={link}>{label}</NavLink>)}</nav>
          <div className="flex items-center gap-2">
            <ModeSwitch />
            {user
              ? <Link to="/dashboard" className="btn-secondary hidden sm:inline-flex"><LayoutDashboard className="h-4 w-4" aria-hidden /> {user.display_name || user.username}</Link>
              : <Link to="/login" className="btn-primary hidden sm:inline-flex"><LogIn className="h-4 w-4" aria-hidden /> Sign in</Link>}
            <button className="btn-ghost xl:hidden" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="mobile-nav" onClick={() => setOpen((o) => !o)}>
              {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>
        {open && (
          <nav id="mobile-nav" aria-label="Mobile" className="border-t border-line bg-ivory px-4 pb-4 xl:hidden">
            <ul className="grid grid-cols-2 gap-1 pt-3">
              {NAV.map(([to, label]) => <li key={to}><NavLink to={to} onClick={() => setOpen(false)} className={link}>{label}</NavLink></li>)}
              <li><NavLink to={user ? "/dashboard" : "/login"} onClick={() => setOpen(false)} className={link}>{user ? "Dashboard" : "Sign in"}</NavLink></li>
              {user && <li><NavLink to="/settings" onClick={() => setOpen(false)} className={link}>Settings</NavLink></li>}
              {user && <li><NavLink to="/admin/bodhan-health" onClick={() => setOpen(false)} className={link}>Bodhan Health</NavLink></li>}
            </ul>
          </nav>
        )}
      </header>
      <main id="main" key={loc.pathname} className="flex-1" tabIndex={-1}><Outlet /></main>
      <footer className="border-t border-line bg-sand/60">
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-10 sm:grid-cols-3 sm:px-6">
          <div><Logo /><p className="mt-3 text-sm text-clay-deep/80">Every story deserves to live beyond its generation.</p></div>
          <div className="text-sm"><p className="eyebrow mb-2">Explore</p><ul className="space-y-1"><li><Link to="/explore">Heritage Explorer</Link></li><li><Link to="/map">Heritage Map</Link></li><li><Link to="/timeline">Timeline</Link></li><li><Link to="/try">Try HeritageLens</Link></li></ul></div>
          <div className="text-sm"><p className="eyebrow mb-2">Preserve</p><ul className="space-y-1"><li><Link to="/preserve">Preserve a story</Link></li><li><Link to="/digitizer">Document Digitizer</Link></li><li><Link to="/future">Message to the Future</Link></li><li><Link to="/settings">Settings</Link></li><li><Link to="/admin/bodhan-health">Bodhan API Health</Link></li></ul></div>
        </div>
        <p className="border-t border-line py-4 text-center text-xs text-mute">Language AI by Bodhan, called only from the HeritageLens server. Map © OpenStreetMap contributors.</p>
      </footer>
    </div>
  );
}
