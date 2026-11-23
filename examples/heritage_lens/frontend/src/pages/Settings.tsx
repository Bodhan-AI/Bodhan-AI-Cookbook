import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CheckCircle2, XCircle } from "lucide-react";
import { Alert, Button, Container, Field, PageHeader, Select, TextArea, TextInput } from "../components/ui";
import { useAuth } from "../lib/auth";
import { useMode } from "../lib/mode";
import { api } from "../lib/api";
import { useCapabilities } from "../lib/caps";

export default function Settings() {
  const { user, setUser, logout } = useAuth();
  const { mode, setMode, status, statusError, refreshStatus } = useMode();
  const { caps } = useCapabilities();
  const nav = useNavigate();
  const [name, setName] = useState(user?.display_name ?? ""); const [bio, setBio] = useState(user?.bio ?? "");
  const [lang, setLang] = useState(user?.preferred_language ?? "");
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null);
    try { setUser(await api.updateMe({ display_name: name || null, bio: bio || null, preferred_language: lang || null })); setMsg({ ok: true, text: "Profile saved." }); }
    catch (x) { setMsg({ ok: false, text: (x as Error).message }); } finally { setBusy(false); }
  };
  return (
    <>
      <PageHeader eyebrow="Settings" title="Profile and settings" />
      <Container className="grid gap-8 lg:grid-cols-2">
        <form onSubmit={save} className="card space-y-4 p-6">
          <h2 className="text-xl">Your profile</h2>
          <p className="text-sm">Signed in as <strong>{user?.username}</strong> ({user?.email}).</p>
          {msg && <Alert kind={msg.ok ? "success" : "error"}>{msg.text}</Alert>}
          <Field label="Display name" hint="Shown as the default attribution on stories you publish.">{(id) => <TextInput id={id} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />}</Field>
          <Field label="About you">{(id) => <TextArea id={id} rows={3} maxLength={1000} value={bio} onChange={(e) => setBio(e.target.value)} />}</Field>
          <Field label="Preferred language">{(id) => <Select id={id} value={lang} onChange={(e) => setLang(e.target.value)}><option value="">Not set</option>{Object.entries(caps?.services["indic-transcribe"].languages ?? {}).map(([c, n]) => <option key={c} value={c}>{n}</option>)}</Select>}</Field>
          <div className="flex gap-3"><Button type="submit" loading={busy}>Save profile</Button><Button type="button" variant="secondary" onClick={() => { logout(); nav("/"); }}>Sign out</Button></div>
        </form>
        <div className="space-y-6">
          <section className="card p-6"><h2 className="text-xl">Demo or Live mode</h2>
            <p className="mt-2 text-sm">Demo Mode shows labelled sample records and never calls Bodhan. Live Mode uses your real data and real Bodhan calls made by the server. It is never switched automatically.</p>
            <div className="mt-3 flex gap-2">{(["demo", "live"] as const).map((m) => <Button key={m} variant={mode === m ? "primary" : "secondary"} aria-pressed={mode === m} onClick={() => setMode(m)}>{m === "demo" ? "Demo" : "Live"}</Button>)}</div></section>
          <section className="card p-6"><div className="flex items-center justify-between"><h2 className="text-xl">Server configuration</h2><Button variant="ghost" onClick={refreshStatus}>Refresh</Button></div>
            {statusError && <Alert kind="error">{statusError}</Alert>}
            {status && <><ul className="mt-3 space-y-2 text-sm">{Object.entries(status.services).map(([n, s]) => <li key={n} className="flex items-center gap-2">{s.configured ? <CheckCircle2 className="h-4 w-4 text-emerald-700" aria-label="configured" /> : <XCircle className="h-4 w-4 text-red-700" aria-label="not configured" />}<code>{n}</code> <span className="text-mute">{s.configured ? `key set (${s.key_source})` : "no key"}</span></li>)}</ul>
              <p className="mt-3 text-xs">FFmpeg: {status.ffmpeg.available ? "available" : "not found (audio needs it)"} · PDF support: {status.pdf_support ? "yes" : "no"}. “Configured” means a key is present, not that Bodhan accepted it. Key values are never shown. Add keys to <code>backend/.env</code> and restart the backend.</p></>}
            {caps && <p className="mt-2 text-xs">Capability tables loaded from the server.</p>}
            <div className="mt-4 border-t border-line pt-3">
              <Link to="/admin/bodhan-health" className="btn-secondary text-xs inline-flex items-center gap-1.5">
                Open Bodhan API Health Dashboard →
              </Link>
            </div>
          </section>
          <section className="card p-6"><h2 className="text-xl">Not available yet</h2><p className="mt-2 text-sm">Email verification, password reset and account deletion are not implemented.</p></section>
        </div>
      </Container>
    </>
  );
}
