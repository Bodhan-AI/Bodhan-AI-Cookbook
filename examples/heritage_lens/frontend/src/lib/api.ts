import type {
  Audio, BodhanServiceHealth, Bundle, Capabilities, Capsule, ConfigStatus, Dashboard, DocumentRec, Facets, ImageRec, MapItem, Page, Story,
  TokenResponse, TranslateResult, Translation, TransliterateResult, Transliteration, User,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";
const TOKEN_KEY = "hl_token";

export const tokenStore = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t: string) => { try { localStorage.setItem(TOKEN_KEY, t); } catch { /* storage blocked */ } },
  clear: () => { try { localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ } },
};

export class ApiError extends Error {
  status: number; code: string; requestId?: string; details?: unknown;
  constructor(status: number, code: string, message: string, requestId?: string, details?: unknown) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code; this.requestId = requestId; this.details = details;
  }
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    const e = body?.error;
    if (e) {
      const detail = Array.isArray(e.details) && e.details.length ? ` (${e.details.map((d: { loc: string[]; message: string }) => `${d.loc.slice(1).join(".")}: ${d.message}`).join("; ")})` : "";
      return new ApiError(res.status, e.code ?? "error", `${e.message}${detail}`, e.request_id, e.details);
    }
  } catch { /* not JSON */ }
  return new ApiError(res.status, "http_error", `The server returned an error (${res.status}).`);
}

interface Opts { method?: string; json?: unknown; form?: FormData; auth?: boolean; signal?: AbortSignal; query?: Record<string, string | number | boolean | undefined | null> }

function url(path: string, query?: Opts["query"]) {
  const q = new URLSearchParams();
  Object.entries(query ?? {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") q.set(k, String(v)); });
  const s = q.toString();
  return `${BASE}/api${path}${s ? `?${s}` : ""}`;
}

function headers(opts: Opts): Headers {
  const h = new Headers();
  const t = tokenStore.get();
  if (t && opts.auth !== false) h.set("Authorization", `Bearer ${t}`);
  if (opts.json !== undefined) h.set("Content-Type", "application/json");
  return h;
}

async function send(path: string, opts: Opts = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url(path, opts.query), {
      method: opts.method ?? (opts.json !== undefined || opts.form ? "POST" : "GET"),
      headers: headers(opts), signal: opts.signal,
      body: opts.form ?? (opts.json !== undefined ? JSON.stringify(opts.json) : undefined),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Could not reach the HeritageLens server. Check that the backend is running and try again.");
  }
  if (!res.ok) {
    const err = await toError(res);
    if (res.status === 401 && tokenStore.get() && path !== "/auth/login") window.dispatchEvent(new Event("hl:unauthorized"));
    throw err;
  }
  return res;
}

async function request<T>(path: string, opts: Opts = {}): Promise<T> {
  const res = await send(path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Upload with real progress (fetch cannot report upload progress). */
export function upload<T>(path: string, form: FormData, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url(path));
    const t = tokenStore.get();
    if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Upload failed: could not reach the server."));
    xhr.onload = () => {
      let body: any = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as T);
      const e = body?.error;
      reject(new ApiError(xhr.status, e?.code ?? "http_error", e?.message ?? `Upload failed (${xhr.status}).`, e?.request_id));
    };
    xhr.send(form);
  });
}

export const blob = async (path: string, signal?: AbortSignal): Promise<Blob> => (await send(path, { signal })).blob();

const get = <T,>(path: string, query?: Opts["query"], signal?: AbortSignal) => request<T>(path, { query, signal });
const post = <T,>(path: string, json?: unknown) => request<T>(path, { method: "POST", json: json ?? {} });
const put = <T,>(path: string, json: unknown) => request<T>(path, { method: "PUT", json });
const del = (path: string, query?: Opts["query"]) => request<void>(path, { method: "DELETE", query });

export const api = {
  // auth
  register: (b: { username: string; email: string; password: string }) => request<TokenResponse>("/auth/register", { json: b, auth: false }),
  login: (b: { email: string; password: string }) => request<TokenResponse>("/auth/login", { json: b, auth: false }),
  me: () => get<User>("/auth/me"),
  updateMe: (b: Partial<Pick<User, "display_name" | "bio" | "preferred_language">>) => put<User>("/auth/me", b),
  // status
  configStatus: () => get<ConfigStatus>("/config/status"),
  capabilities: () => get<Capabilities>("/ai/capabilities"),
  // stories
  stories: (q: Record<string, string | number | undefined>) => get<Page<Story>>("/stories", q),
  story: (id: number | string) => get<Story>(`/stories/${id}`),
  bundle: (id: number | string) => get<Bundle>(`/stories/${id}/bundle`),
  createStory: (b: Partial<Story>) => post<Story>("/stories", b),
  updateStory: (id: number | string, b: Partial<Story>) => put<Story>(`/stories/${id}`, b),
  deleteStory: (id: number | string, deleteMedia = false) => del(`/stories/${id}`, { delete_media: deleteMedia }),
  facets: () => get<Facets>("/discover/facets"),
  locations: (category?: string) => get<{ items: MapItem[]; total_public: number }>("/locations", { category }),
  timeline: (q: { country?: string; language?: string }) => get<{ dated: Story[]; undated: Story[] }>("/timeline", q),
  // saved
  saved: () => get<Page<Story>>("/saved", { page_size: 100 }),
  savedIds: () => get<{ story_ids: number[] }>("/saved/ids"),
  saveStory: (id: number | string) => request<void>(`/saved/${id}`, { method: "PUT", json: {} }),
  unsaveStory: (id: number | string) => del(`/saved/${id}`),
  // audio
  audioList: (q: { kind?: string; story_id?: number; page?: number; page_size?: number } = {}) => get<Page<Audio>>("/audio", { page_size: 50, ...q }),
  audioUpload: (file: Blob, name: string, o: { story_id?: number; language?: string }, onProgress?: (f: number) => void) => {
    const f = new FormData(); f.append("file", file, name);
    if (o.story_id) f.append("story_id", String(o.story_id));
    if (o.language) f.append("language", o.language);
    return upload<Audio>("/audio", f, onProgress);
  },
  audioEditTranscript: (id: number, text: string) => put<Audio>(`/audio/${id}/transcript`, { transcript_edited: text }),
  audioDelete: (id: number) => del(`/audio/${id}`),
  audioFile: (id: number, which: "auto" | "original" | "generated" = "auto") => `/audio/${id}/file?which=${which}`,
  // documents & images
  documents: (q: { q?: string; story_id?: number } = {}) => get<Page<DocumentRec>>("/documents", { page_size: 100, ...q }),
  document: (id: number) => get<DocumentRec>(`/documents/${id}`),
  documentUpload: (file: File, o: { story_id?: number; document_type?: string }, onProgress?: (f: number) => void) => {
    const f = new FormData(); f.append("file", file, file.name);
    if (o.story_id) f.append("story_id", String(o.story_id));
    if (o.document_type) f.append("document_type", o.document_type);
    return upload<DocumentRec>("/documents", f, onProgress);
  },
  documentEditText: (id: number, text: string) => put<DocumentRec>(`/documents/${id}/text`, { extracted_edited_text: text }),
  documentDelete: (id: number) => del(`/documents/${id}`),
  documentFile: (id: number) => `/documents/${id}/file`,
  images: (story_id: number) => get<Page<ImageRec>>("/images", { story_id }),
  imageUpload: (file: File, o: { story_id: number; caption?: string; attribution?: string }, onProgress?: (f: number) => void) => {
    const f = new FormData(); f.append("file", file, file.name); f.append("story_id", String(o.story_id));
    if (o.caption) f.append("caption", o.caption);
    if (o.attribution) f.append("attribution", o.attribution);
    return upload<ImageRec>("/images", f, onProgress);
  },
  imageDelete: (id: number) => del(`/images/${id}`),
  imageFile: (id: number) => `/images/${id}/file`,
  // derived
  translations: (story_id?: number) => get<Page<Translation>>("/translations", { story_id, page_size: 100 }),
  editTranslation: (id: number, text: string) => put<Translation>(`/translations/${id}`, { edited_text: text }),
  deleteTranslation: (id: number) => del(`/translations/${id}`),
  transliterations: () => get<Page<Transliteration>>("/transliterations", { page_size: 100 }),
  deleteTransliteration: (id: number) => del(`/transliterations/${id}`),
  // capsules
  capsules: () => get<Page<Capsule>>("/time-capsules", { page_size: 100 }),
  createCapsule: (b: Partial<Capsule>) => post<Capsule>("/time-capsules", b),
  updateCapsule: (id: number, b: Partial<Capsule>) => put<Capsule>(`/time-capsules/${id}`, b),
  deleteCapsule: (id: number) => del(`/time-capsules/${id}`),
  capsuleAudioUpload: (id: number, file: Blob, name: string, onProgress?: (f: number) => void) => {
    const f = new FormData(); f.append("file", file, name);
    return upload<Capsule>(`/time-capsules/${id}/audio`, f, onProgress);
  },
  capsuleAudioFile: (id: number) => `/time-capsules/${id}/audio`,
  dashboard: () => get<Dashboard>("/dashboard"),
  // Bodhan (all through the backend)
  transcribe: (b: { audio_id: number; language: string; force?: boolean }) => post<Audio>("/ai/transcribe", b),
  translate: (b: { story_id?: number; text?: string; target_language: string; script?: string; save?: boolean }) => post<TranslateResult>("/ai/translate", b),
  transliterate: (b: { story_id?: number; text: string; language: string; script: string; save?: boolean }) => post<TransliterateResult>("/ai/transliterate", b),
  ocr: (b: { document_id: number; table_format?: string; force?: boolean }) => post<DocumentRec>("/ai/ocr", b),
  speak: (b: { story_id?: number; translation_id?: number; text?: string; language: string; voice: string; style?: string | null }) => post<Audio>("/ai/speak", b),
  speakPreview: async (b: { text: string; language: string; voice: string; style?: string | null }) => (await send("/ai/speak/preview", { method: "POST", json: b })).blob(),
  // Bodhan API Health diagnostics
  bodhanHealth: () => get<{ services: BodhanServiceHealth[]; checked_at: string }>("/health/bodhan"),
  testBodhanService: (serviceName: string) => post<BodhanServiceHealth>(`/health/bodhan/test/${serviceName}`),
  testAllBodhanServices: () => post<{ services: BodhanServiceHealth[]; checked_at: string }>("/health/bodhan/test-all"),
};
