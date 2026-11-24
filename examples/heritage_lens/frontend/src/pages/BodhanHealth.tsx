import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Play,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { Alert, Button, Container, ErrorAlert, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { fmtDate } from "../lib/format";
import type { BodhanServiceHealth } from "../lib/types";

const SERVICE_META: Record<string, { label: string; endpoint: string; desc: string }> = {
  "indic-speak": {
    label: "indic-speak (Text-to-Speech)",
    endpoint: "POST /v1/audio/speech",
    desc: "Reads text aloud in 22 Indian languages and English. Returns PCM16 24kHz mono WAV.",
  },
  "indic-transcribe": {
    label: "indic-transcribe (Speech-to-Text)",
    endpoint: "POST /v1/audio/transcriptions",
    desc: "Transcribes audio up to 30s per request across 25 Indian languages and English.",
  },
  "indic-translate": {
    label: "indic-translate (Translation)",
    endpoint: "POST /translate & /v1/chat/completions",
    desc: "Translates text across 22 Indian languages and English, supporting native and Roman scripts.",
  },
  "indic-transliterate": {
    label: "indic-transliterate (Transliteration)",
    endpoint: "POST /transliterate & /v1/chat/completions",
    desc: "Converts text between native scripts and Roman letters without changing the language.",
  },
  "indic-ocr": {
    label: "indic-ocr (Document OCR)",
    endpoint: "POST /v1/chat/completions",
    desc: "Extracts reading-ordered Markdown and layout blocks from PNG/JPEG scanned documents.",
  },
};

function ServiceCard({
  item,
  onTest,
  isTesting,
}: {
  item: BodhanServiceHealth;
  onTest: (service: string) => Promise<void>;
  isTesting: boolean;
}) {
  const meta = SERVICE_META[item.service] ?? {
    label: item.service,
    endpoint: "Bodhan AI Service",
    desc: "Language processing service.",
  };

  const getStatusBadge = () => {
    if (!item.configured) {
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800">
          <AlertTriangle className="h-3.5 w-3.5" /> Not Configured
        </span>
      );
    }
    if (item.last_result === "pass") {
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-800">
          <CheckCircle2 className="h-3.5 w-3.5" /> Operational
        </span>
      );
    }
    if (item.last_result === "fail") {
      return (
        <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-semibold text-red-800">
          <XCircle className="h-3.5 w-3.5" /> Error
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-semibold text-blue-800">
        <Clock className="h-3.5 w-3.5" /> Untested
      </span>
    );
  };

  return (
    <article className="card flex flex-col justify-between p-6 transition-shadow hover:shadow-md">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h3 className="text-lg font-bold text-ink">{meta.label}</h3>
            <p className="font-mono text-xs text-clay-deep/75">{meta.endpoint}</p>
          </div>
          <div>{getStatusBadge()}</div>
        </div>

        <p className="mt-2 text-xs text-clay-deep/80">{meta.desc}</p>

        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-3 text-xs">
          <div>
            <span className="text-mute block font-medium">API Key</span>
            {item.configured ? (
              <span className="inline-flex items-center gap-1 font-semibold text-emerald-700">
                <CheckCircle2 className="h-3.5 w-3.5" /> Configured
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 font-semibold text-red-700">
                <XCircle className="h-3.5 w-3.5" /> Missing
              </span>
            )}
          </div>
          <div>
            <span className="text-mute block font-medium">Last Verification</span>
            <span className="text-ink">
              {item.last_verified_at ? fmtDate(item.last_verified_at) : "Never verified"}
            </span>
          </div>
          <div>
            <span className="text-mute block font-medium">Last Test Result</span>
            <span
              className={`font-semibold capitalize ${
                item.last_result === "pass"
                  ? "text-emerald-700"
                  : item.last_result === "fail"
                  ? "text-red-700"
                  : "text-mute"
              }`}
            >
              {item.last_result}
            </span>
          </div>
          <div>
            <span className="text-mute block font-medium">HTTP Status</span>
            <span className="font-mono text-ink">
              {item.http_status ? `${item.http_status}` : item.last_result === "untested" ? "—" : "Error"}
            </span>
          </div>
        </div>

        {item.error_category && (
          <div className="mt-3 rounded-lg bg-red-50 p-2.5 text-xs text-red-800">
            <span className="font-semibold">Error Category:</span>{" "}
            <span className="font-mono">{item.error_category}</span>
          </div>
        )}

        {item.diagnostic_detail && (
          <div className="mt-3">
            <span className="text-mute mb-1 block text-xs font-semibold">Diagnostic Output:</span>
            <pre className="max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-sand/60 p-2.5 font-mono text-xs text-ink/90 border border-line">
              {item.diagnostic_detail}
            </pre>
          </div>
        )}
      </div>

      <div className="mt-5 border-t border-line pt-4 flex items-center justify-between gap-3">
        <span className="text-xs text-mute">
          {item.response_time_ms ? `${item.response_time_ms} ms` : ""}
        </span>
        <Button
          onClick={() => void onTest(item.service)}
          loading={isTesting}
          variant={item.last_result === "pass" ? "secondary" : "primary"}
          className="text-xs"
        >
          <Play className="h-3.5 w-3.5" aria-hidden /> Test Connection
        </Button>
      </div>
    </article>
  );
}

export default function BodhanHealth() {
  const health = useAsync(() => api.bodhanHealth(), []);
  const [testingService, setTestingService] = useState<string | null>(null);
  const [testingAll, setTestingAll] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);

  const testSingle = async (serviceName: string) => {
    setTestingService(serviceName);
    setTestError(null);
    try {
      const updated = await api.testBodhanService(serviceName);
      if (health.data) {
        health.setData({
          ...health.data,
          services: health.data.services.map((s) => (s.service === updated.service ? updated : s)),
        });
      }
    } catch (e) {
      setTestError((e as Error).message);
    } finally {
      setTestingService(null);
    }
  };

  const testAll = async () => {
    setTestingAll(true);
    setTestError(null);
    try {
      const res = await api.testAllBodhanServices();
      health.setData(res);
    } catch (e) {
      setTestError((e as Error).message);
    } finally {
      setTestingAll(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Administrator Diagnostics"
        title="Bodhan API Health"
      >
        <div className="space-y-3">
          <p>
            Live diagnostic verification for the five Bodhan AI services. Do not claim that a service
            is live verified merely because its API key exists in configuration — each card requires a real
            test request. API keys are strictly kept on the server and are never revealed.
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              variant="secondary"
              onClick={() => void health.reload()}
              disabled={health.loading || testingAll}
            >
              <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
            </Button>
            <Button
              onClick={() => void testAll()}
              loading={testingAll}
              disabled={health.loading || testingService !== null}
            >
              <Activity className="h-4 w-4" aria-hidden /> Test All Services
            </Button>
          </div>
        </div>
      </PageHeader>

      <Container className="space-y-6">
        <ErrorAlert error={health.error} onRetry={health.reload} />
        {testError && <Alert kind="error" title="Connection Test Failed">{testError}</Alert>}

        <Alert kind="info" title="Zero Credential Leakage Guarantee">
          Service keys are securely loaded from <code>backend/.env</code> and never exposed in API responses, logs, or frontend code. Test requests use small, valid inputs with credit-safe limits.
        </Alert>

        {health.loading && (
          <div className="flex justify-center p-12">
            <Spinner label="Loading Bodhan health status" />
          </div>
        )}

        {health.data && (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {health.data.services.map((item) => (
              <ServiceCard
                key={item.service}
                item={item}
                onTest={testSingle}
                isTesting={testingService === item.service || testingAll}
              />
            ))}
          </div>
        )}

        <div className="card p-6">
          <h2 className="text-lg font-bold">Key Configuration Guide</h2>
          <p className="mt-1 text-sm text-clay-deep/85">
            Configure your separate Bodhan API keys in <code>heritagelens/backend/.env</code>:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-ink p-4 font-mono text-xs text-emerald-400">
{`BODHAN_OCR_API_KEY=your_ocr_key
BODHAN_TRANSCRIBE_API_KEY=your_transcribe_key
BODHAN_TRANSLATE_API_KEY=your_translate_key
BODHAN_TRANSLITERATE_API_KEY=your_transliterate_key
BODHAN_SPEAK_API_KEY=your_speak_key`}
          </pre>
          <p className="mt-3 text-xs text-mute">
            After editing <code>.env</code>, restart the backend server. You can also run the CLI verification script: <code>python scripts/verify_bodhan.py</code>.
          </p>
        </div>
      </Container>
    </>
  );
}
