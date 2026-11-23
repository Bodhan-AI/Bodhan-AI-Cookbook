import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Alert, Button, Field, TextInput } from "../components/ui";
import { useAuth } from "../lib/auth";

export function validateRegistration(v: { username: string; email: string; password: string; confirm: string }) {
  const e: Record<string, string> = {};
  if (!/^[A-Za-z0-9_.-]{3,50}$/.test(v.username.trim())) e.username = "3–50 characters: letters, digits, dot, underscore or hyphen.";
  if (!/^\S+@\S+\.\S+$/.test(v.email.trim())) e.email = "Enter a valid email address.";
  if (v.password.length < 8) e.password = "Use at least 8 characters.";
  else if (new TextEncoder().encode(v.password).length > 72) e.password = "Password is too long (72 bytes maximum).";
  if (v.confirm !== v.password) e.confirm = "Passwords do not match.";
  return e;
}

function Shell({ title, children, footer }: { title: string; children: React.ReactNode; footer: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-md px-4 py-14">
      <div className="card p-7"><h1 className="text-3xl">{title}</h1><div className="mt-5">{children}</div></div>
      <p className="mt-4 text-center text-sm">{footer}</p>
    </div>
  );
}

export function Login() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/dashboard";
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  if (user) return <Navigate to={from} replace />;
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null); setBusy(true);
    try { await login(email.trim(), password); nav(from, { replace: true }); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <Shell title="Sign in" footer={<>New here? <Link to="/register" className="font-semibold underline">Create an account</Link></>}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {err && <Alert kind="error">{err}</Alert>}
        <Field label="Email">{(id) => <TextInput id={id} type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label="Password">{(id) => <TextInput id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}</Field>
        <Button type="submit" loading={busy} className="w-full" disabled={!email || !password}>Sign in</Button>
        <p className="text-xs text-mute">Password reset and email verification are not available yet because no email service is configured.</p>
      </form>
    </Shell>
  );
}

export function Register() {
  const { user, register } = useAuth();
  const nav = useNavigate();
  const [v, setV] = useState({ username: "", email: "", password: "", confirm: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  if (user) return <Navigate to="/dashboard" replace />;
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => setV({ ...v, [k]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    const found = validateRegistration(v); setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    try { await register(v.username.trim(), v.email.trim(), v.password); nav("/dashboard", { replace: true }); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return (
    <Shell title="Create your archive" footer={<>Already registered? <Link to="/login" className="font-semibold underline">Sign in</Link></>}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {err && <Alert kind="error">{err}</Alert>}
        <Field label="Username" error={errors.username}>{(id, d) => <TextInput id={id} autoComplete="username" value={v.username} onChange={set("username")} invalid={!!errors.username} aria-describedby={d} />}</Field>
        <Field label="Email" error={errors.email}>{(id, d) => <TextInput id={id} type="email" autoComplete="email" value={v.email} onChange={set("email")} invalid={!!errors.email} aria-describedby={d} />}</Field>
        <Field label="Password" hint="At least 8 characters." error={errors.password}>{(id, d) => <TextInput id={id} type="password" autoComplete="new-password" value={v.password} onChange={set("password")} invalid={!!errors.password} aria-describedby={d} />}</Field>
        <Field label="Confirm password" error={errors.confirm}>{(id, d) => <TextInput id={id} type="password" autoComplete="new-password" value={v.confirm} onChange={set("confirm")} invalid={!!errors.confirm} aria-describedby={d} />}</Field>
        <Button type="submit" loading={busy} className="w-full">Create account</Button>
      </form>
    </Shell>
  );
}
