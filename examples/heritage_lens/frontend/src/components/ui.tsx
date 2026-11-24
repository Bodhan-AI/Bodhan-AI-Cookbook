import { forwardRef, useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, RefreshCw, FlaskConical, Radio, UserRound, Sparkles } from "lucide-react";
import type { Provenance } from "../lib/types";
import { useMode } from "../lib/mode";

export const Spinner = ({ label = "Loading" }: { label?: string }) => (
  <span role="status" className="inline-flex items-center gap-2 text-sm text-clay">
    <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden /> {label}…
  </span>
);

export function Button({ variant = "primary", loading, children, className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; loading?: boolean }) {
  return (
    <button {...rest} disabled={rest.disabled || loading} aria-busy={loading || undefined} className={`btn-${variant} ${className}`}>
      {loading && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}
      {children}
    </button>
  );
}

export function Alert({ kind = "info", title, children, onRetry }: { kind?: "info" | "error" | "success" | "warn"; title?: string; children?: ReactNode; onRetry?: () => void }) {
  const styles = { info: "border-clay-muted bg-sand text-clay-deep", error: "border-red-300 bg-red-50 text-red-900", success: "border-emerald-300 bg-emerald-50 text-emerald-900", warn: "border-amber-300 bg-amber-50 text-amber-900" }[kind];
  const Icon = { info: Info, error: AlertTriangle, success: CheckCircle2, warn: AlertTriangle }[kind];
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`flex gap-3 rounded-xl border p-3.5 text-sm ${styles}`}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="break-words">{children}</div>}
        {onRetry && <button onClick={onRetry} className="mt-2 inline-flex items-center gap-1.5 font-semibold underline"><RefreshCw className="h-3.5 w-3.5" aria-hidden /> Try again</button>}
      </div>
    </div>
  );
}

export const ErrorAlert = ({ error, onRetry }: { error: Error | string | null | undefined; onRetry?: () => void }) =>
  error ? <Alert kind="error" title="Something went wrong" onRetry={onRetry}>{typeof error === "string" ? error : error.message}</Alert> : null;

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="paper flex flex-col items-center px-6 py-12 text-center">
      {icon && <div className="mb-3 text-clay-soft">{icon}</div>}
      <h3 className="text-xl">{title}</h3>
      {children && <p className="mt-2 max-w-md text-sm text-clay-deep/80">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

type FieldProps = { label: string; hint?: string; error?: string; children: (id: string, describedBy?: string) => ReactNode };
export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId();
  const desc = [hint ? `${id}-h` : "", error ? `${id}-e` : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      {children(id, desc)}
      {hint && <p id={`${id}-h`} className="mt-1 text-xs text-mute">{hint}</p>}
      {error && <p id={`${id}-e`} role="alert" className="mt-1 text-xs font-semibold text-red-800">{error}</p>}
    </div>
  );
}
export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(({ invalid, className = "", ...p }, ref) =>
  <input ref={ref} aria-invalid={invalid || undefined} className={`input ${invalid ? "border-red-500" : ""} ${className}`} {...p} />);
export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className = "", ...p }, ref) =>
  <textarea ref={ref} className={`input indic ${className}`} {...p} />);
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className = "", children, ...p }, ref) =>
  <select ref={ref} className={`input ${className}`} {...p}>{children}</select>);

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;
  return (
    <nav aria-label="Pagination" className="mt-8 flex items-center justify-center gap-3">
      <Button variant="secondary" disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</Button>
      <span className="text-sm" aria-live="polite">Page {page} of {pages}</span>
      <Button variant="secondary" disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</Button>
    </nav>
  );
}

const PROV: Record<Provenance, { label: string; cls: string; Icon: typeof Info; title: string }> = {
  demo: { label: "DEMO", cls: "border-dashed border-clay bg-sand text-clay-deep", Icon: FlaskConical, title: "Sample content. Not real archive data and not Bodhan output." },
  illustrative: { label: "ILLUSTRATIVE", cls: "border-dashed border-clay bg-sand text-clay-deep", Icon: Sparkles, title: "Hand-written example shown to explain a feature. Not an actual API result." },
  live_bodhan: { label: "LIVE · Bodhan", cls: "border-emerald-400 bg-emerald-50 text-emerald-900", Icon: Radio, title: "Produced by a real Bodhan API call made by the server." },
  user_authored: { label: "Contributor-authored", cls: "border-line bg-white text-clay-deep", Icon: UserRound, title: "Written or recorded by a person." },
};
export function ProvenanceBadge({ value }: { value?: Provenance | null }) {
  if (!value) return null;
  const p = PROV[value];
  return (
    <span title={p.title} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold tracking-wide ${p.cls}`}>
      <p.Icon className="h-3 w-3" aria-hidden /> {p.label}
    </span>
  );
}

export function PageHeader({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: ReactNode }) {
  return (
    <header className="mx-auto max-w-6xl px-4 pb-6 pt-10 sm:px-6">
      {eyebrow && <p className="eyebrow">{eyebrow}</p>}
      <h1 className="mt-1 text-3xl sm:text-4xl">{title}</h1>
      {children && <div className="mt-3 max-w-3xl text-clay-deep/85">{children}</div>}
    </header>
  );
}
export const Container = ({ children, className = "" }: { children: ReactNode; className?: string }) =>
  <div className={`mx-auto max-w-6xl px-4 pb-16 sm:px-6 ${className}`}>{children}</div>;

/** Gate for AI actions. In Demo Mode no Bodhan call is ever made. */
export function LiveGate({ children, what = "This action" }: { children: ReactNode; what?: string }) {
  const { live, status, statusError } = useMode();
  if (!live) {
    return <Alert kind="info" title="Demo Mode is on">{what} would call Bodhan AI. Demo Mode never calls Bodhan or uses credits. Switch to <strong>Live</strong> in the header to run it on your own data.</Alert>;
  }
  return (
    <>
      {statusError && <div className="mb-3"><Alert kind="warn" title="Cannot read server status">{statusError}</Alert></div>}
      {status?.none_configured && <div className="mb-3"><Alert kind="warn" title="No Bodhan keys configured on the server">Live actions will fail with an explicit error until keys are added to backend/.env.</Alert></div>}
      {children}
    </>
  );
}
