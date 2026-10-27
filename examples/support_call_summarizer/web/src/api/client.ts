// API client for the Support Call Summarizer backend.
// Only uses fetch, FormData and XMLHttpRequest so it also runs in React Native.

import type {
  AppConfig,
  CreateCallResponse,
  ExportFormat,
  HealthResponse,
  JobView,
  Language,
  LanguagesResponse,
} from "./types";

export interface ApiClientOptions {
  /** "" (default) means same origin. No trailing slash needed. */
  baseUrl?: string;
  /** Sent as X-App-Key on every request when set. */
  appKey?: string;
  fetchImpl?: typeof fetch;
  createXhr?: () => XMLHttpRequest;
}

/** A web Blob/File, or a React Native file descriptor ({ uri, name, type }). */
export type UploadFile = Blob | { uri: string; name: string; type: string };

export interface UploadCallInput {
  file: UploadFile;
  /** Required for plain Blobs; Files and RN descriptors carry their own name. */
  fileName?: string;
  language?: string | null;
  ticketId?: string | null;
  agentName?: string | null;
}

export interface UploadOptions {
  /** Called with a fraction between 0 and 1. */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

export interface ExportDownload {
  blob: Blob;
  filename: string;
}

export const NETWORK_ERROR_CODE = "network_error";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  /** True when the request never got an HTTP response. */
  get isNetworkError(): boolean {
    return this.status === 0;
  }

  /** Network failures and 5xx responses are worth retrying. */
  get isRetryable(): boolean {
    return this.status === 0 || this.status >= 500;
  }
}

export function isAbortError(err: unknown): boolean {
  return typeof err === "object" && err !== null && (err as { name?: unknown }).name === "AbortError";
}

function abortError(): Error {
  const err = new Error("The request was cancelled.");
  err.name = "AbortError";
  return err;
}

function networkError(): ApiError {
  return new ApiError(0, NETWORK_ERROR_CODE, "Could not reach the server. Check your connection and try again.");
}

function fallbackMessage(status: number): string {
  if (status === 404) return "Not found.";
  if (status === 401 || status === 403) return "This app is not authorised to use the server.";
  if (status === 413) return "The file is too large for the server to accept.";
  if (status >= 500) return `The server had a problem (HTTP ${status}). Please try again.`;
  return `The request failed (HTTP ${status}).`;
}

/** Builds an ApiError from a status code and the raw response body text. */
export function errorFromBody(status: number, bodyText: string | null | undefined): ApiError {
  if (bodyText) {
    try {
      const parsed: unknown = JSON.parse(bodyText);
      const error = (parsed as { error?: { code?: unknown; message?: unknown } } | null)?.error;
      if (error && typeof error.message === "string") {
        const code = typeof error.code === "string" ? error.code : `http_${status}`;
        return new ApiError(status, code, error.message);
      }
    } catch {
      // Not JSON (e.g. a proxy's HTML error page); fall through.
    }
  }
  return new ApiError(status, status === 404 ? "not_found" : `http_${status}`, fallbackMessage(status));
}

/** Extracts the filename from a Content-Disposition header (RFC 6266, incl. filename*). */
export function filenameFromContentDisposition(header: string | null | undefined): string | null {
  if (!header) return null;
  let name: string | null = null;

  const extended = /filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i.exec(header);
  if (extended?.[2]) {
    try {
      name = decodeURIComponent(extended[2].trim().replace(/^"|"$/g, ""));
    } catch {
      name = null;
    }
  }
  if (!name) {
    const plain = /(?:^|;)\s*filename\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^;]+))/i.exec(header);
    const raw = plain?.[1] ?? plain?.[2];
    if (raw) name = raw.replace(/\\(.)/g, "$1").trim();
  }
  if (!name) return null;
  // Never let a header choose a directory.
  const base = name.split(/[\\/]/).pop()?.trim() ?? "";
  return base || null;
}

function isRnFile(file: UploadFile): file is { uri: string; name: string; type: string } {
  return typeof file === "object" && file !== null && "uri" in file;
}

export interface ApiClient {
  readonly baseUrl: string;
  health(signal?: AbortSignal): Promise<HealthResponse>;
  getConfig(signal?: AbortSignal): Promise<AppConfig>;
  getLanguages(signal?: AbortSignal): Promise<Language[]>;
  uploadCall(input: UploadCallInput, options?: UploadOptions): Promise<CreateCallResponse>;
  getCall(callId: string, signal?: AbortSignal): Promise<JobView>;
  exportUrl(callId: string, format: ExportFormat): string;
  downloadExport(callId: string, format: ExportFormat, signal?: AbortSignal): Promise<ExportDownload>;
}

const EXPORT_EXTENSIONS: Record<ExportFormat, string> = {
  json: "json",
  calls_csv: "csv",
  segments_csv: "csv",
};

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const baseUrl = (options.baseUrl ?? "").replace(/\/+$/, "");
  const appKey = options.appKey || undefined;
  const doFetch: typeof fetch = options.fetchImpl ?? ((input, init) => fetch(input, init));
  const createXhr = options.createXhr ?? (() => new XMLHttpRequest());

  const url = (path: string) => `${baseUrl}${path}`;

  function headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = { ...extra };
    if (appKey) h["X-App-Key"] = appKey;
    return h;
  }

  async function request(path: string, signal?: AbortSignal, accept = "application/json"): Promise<Response> {
    let response: Response;
    try {
      response = await doFetch(url(path), { headers: headers({ Accept: accept }), signal });
    } catch (err) {
      if (isAbortError(err)) throw err;
      throw networkError();
    }
    if (!response.ok) {
      let text: string | null = null;
      try {
        text = await response.text();
      } catch {
        text = null;
      }
      throw errorFromBody(response.status, text);
    }
    return response;
  }

  async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await request(path, signal);
    try {
      return (await response.json()) as T;
    } catch (err) {
      if (isAbortError(err)) throw err;
      throw new ApiError(response.status, "invalid_response", "The server sent a response the app could not read.");
    }
  }

  function uploadCall(input: UploadCallInput, opts: UploadOptions = {}): Promise<CreateCallResponse> {
    const { onProgress, signal } = opts;
    return new Promise<CreateCallResponse>((resolve, reject) => {
      if (signal?.aborted) {
        reject(abortError());
        return;
      }

      const form = new FormData();
      if (isRnFile(input.file)) {
        // React Native's FormData accepts { uri, name, type } descriptors.
        form.append("file", input.file as unknown as Blob);
      } else {
        const ownName = (input.file as Blob & { name?: unknown }).name;
        const name = input.fileName ?? (typeof ownName === "string" ? ownName : "call-audio");
        form.append("file", input.file, name);
      }
      if (input.language) form.append("language", input.language);
      if (input.ticketId?.trim()) form.append("ticket_id", input.ticketId.trim());
      if (input.agentName?.trim()) form.append("agent_name", input.agentName.trim());

      const xhr = createXhr();
      const onAbort = () => xhr.abort();

      xhr.open("POST", url("/api/calls"));
      for (const [k, v] of Object.entries(headers({ Accept: "application/json" }))) {
        xhr.setRequestHeader(k, v);
      }
      if (onProgress && xhr.upload) {
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && event.total > 0) onProgress(Math.min(1, event.loaded / event.total));
        };
        xhr.upload.onload = () => onProgress(1);
      }
      xhr.onload = () => {
        signal?.removeEventListener("abort", onAbort);
        const text = typeof xhr.responseText === "string" ? xhr.responseText : null;
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(text ?? "") as CreateCallResponse);
          } catch {
            reject(new ApiError(xhr.status, "invalid_response", "The server sent a response the app could not read."));
          }
          return;
        }
        reject(xhr.status === 0 ? networkError() : errorFromBody(xhr.status, text));
      };
      xhr.onerror = () => {
        signal?.removeEventListener("abort", onAbort);
        reject(networkError());
      };
      xhr.ontimeout = xhr.onerror;
      xhr.onabort = () => {
        signal?.removeEventListener("abort", onAbort);
        reject(abortError());
      };
      signal?.addEventListener("abort", onAbort);
      xhr.send(form);
    });
  }

  async function downloadExport(callId: string, format: ExportFormat, signal?: AbortSignal): Promise<ExportDownload> {
    const response = await request(exportPath(callId, format), signal, "*/*");
    const blob = await response.blob();
    const filename =
      filenameFromContentDisposition(response.headers.get("Content-Disposition")) ??
      `call-${callId}-${format}.${EXPORT_EXTENSIONS[format]}`;
    return { blob, filename };
  }

  function exportPath(callId: string, format: ExportFormat): string {
    return `/api/calls/${encodeURIComponent(callId)}/export?format=${encodeURIComponent(format)}`;
  }

  return {
    baseUrl,
    health: (signal) => getJson<HealthResponse>("/health", signal),
    getConfig: (signal) => getJson<AppConfig>("/api/config", signal),
    getLanguages: async (signal) => (await getJson<LanguagesResponse>("/api/languages", signal)).languages,
    uploadCall,
    getCall: (callId, signal) => getJson<JobView>(`/api/calls/${encodeURIComponent(callId)}`, signal),
    exportUrl: (callId, format) => url(exportPath(callId, format)),
    downloadExport,
  };
}
