// Centralized, typed client for the CodeAtlas FastAPI backend. The browser only talks to this
// backend (never to Neo4j or the LLM provider directly) and no credentials live in the frontend.

import { ApiError, detailMessage } from "./errors";
import type {
  AnalysisReport, Answer, ArchitectureReport, Explanation, FileEntry, FunctionEntry,
  GraphPayload, Health, ImpactResult, Integrity, ProjectDetail, ProjectStats, ProjectSummary,
  SearchResponse, Tour, TourKind, WriteGuard,
} from "./types";

export const API_BASE: string = (import.meta.env?.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "/api";

type Query = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export interface RequestOptions {
  method?: "GET" | "POST";
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Milliseconds before the request is abandoned (default 60 s). */
  timeoutMs?: number;
}

/** FastAPI list parameters are repeated keys: labels=File&labels=Class. */
export function buildQuery(query?: Query): string {
  if (!query) return "";
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) value.forEach((v) => qs.append(key, String(v)));
    else qs.append(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

function combineSignals(signal: AbortSignal | undefined, timeoutMs: number): { signal: AbortSignal; timedOut: () => boolean } {
  const timeout = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
  return { signal: combined, timedOut: () => timeout.aborted };
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", query, json, body, headers = {}, signal, timeoutMs = 60_000 } = opts;
  const url = `${API_BASE}${path}${buildQuery(query)}`;
  const combined = combineSignals(signal, timeoutMs);
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: json !== undefined ? { "Content-Type": "application/json", ...headers } : headers,
      body: json !== undefined ? JSON.stringify(json) : body,
      signal: combined.signal,
    });
  } catch (err) {
    if (signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
    if (combined.timedOut()) {
      throw new ApiError(0, `The backend did not respond within ${Math.round(timeoutMs / 1000)} s.`, "timeout", null);
    }
    throw new ApiError(
      0,
      "The CodeAtlas API is not reachable. Start it from CodeAtlas/ with the workspace venv active: " +
        "source ../.venv/bin/activate && uvicorn codeatlas.api.main:app --port 8000",
      "network",
      String(err),
    );
  }
  const text = await res.text();
  let data: unknown = text;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const isObj = data !== null && typeof data === "object" && !Array.isArray(data);
    const detail = isObj && "detail" in (data as object) ? (data as { detail: unknown }).detail : data;
    const component = isObj && typeof (data as { component?: unknown }).component === "string"
      ? (data as { component: string }).component
      : null;
    const proxyDown = res.status >= 502 && res.status <= 504 && !isObj;
    throw new ApiError(
      res.status,
      proxyDown
        ? "The CodeAtlas API is not reachable through the dev proxy (is uvicorn running on port 8000?)."
        : detailMessage(detail) || res.statusText || "Request failed",
      proxyDown ? "network" : component,
      detail,
    );
  }
  return data as T;
}

const enc = encodeURIComponent;

export const api = {
  health: (signal?: AbortSignal) => request<Health>("/health", { signal, timeoutMs: 20_000 }),
  projects: (signal?: AbortSignal) => request<ProjectSummary[]>("/projects", { signal, timeoutMs: 20_000 }),
  project: (pid: string, signal?: AbortSignal) => request<ProjectDetail>(`/projects/${enc(pid)}`, { signal }),
  stats: (pid: string, signal?: AbortSignal) => request<ProjectStats>(`/projects/${enc(pid)}/stats`, { signal }),
  integrity: (pid: string, signal?: AbortSignal) => request<Integrity>(`/projects/${enc(pid)}/integrity`, { signal }),
  files: (pid: string, signal?: AbortSignal) => request<FileEntry[]>(`/projects/${enc(pid)}/files`, { signal }),
  functions: (pid: string, signal?: AbortSignal) => request<FunctionEntry[]>(`/projects/${enc(pid)}/functions`, { signal }),

  overview: (p: { project_id: string; labels: string[]; rel_types: string[]; limit: number; after?: string }, signal?: AbortSignal) =>
    request<GraphPayload>("/graph/overview", { query: { ...p, after: p.after ?? "" }, signal }),
  neighborhood: (p: { label: string; node_id: string; depth: number; rel_types: string[]; limit: number }, signal?: AbortSignal) =>
    request<GraphPayload>("/graph/neighborhood", { query: p, signal }),
  path: (p: { mode: "dependency" | "call" | "connection"; from_id: string; to_id: string; from_label?: string; to_label?: string; max_hops?: number }, signal?: AbortSignal) =>
    request<GraphPayload>("/graph/path", { query: p, signal }),
  impact: (p: { label: string; node_id: string; depth: number }, signal?: AbortSignal) =>
    request<ImpactResult>("/graph/impact", { query: p, signal }),

  architecture: (pid: string, signal?: AbortSignal) =>
    request<ArchitectureReport>(`/projects/${enc(pid)}/architecture`, { signal, timeoutMs: 120_000 }),
  tour: (pid: string, p: { kind: TourKind; target?: string | null; narrate?: boolean }, signal?: AbortSignal) =>
    request<Tour>(`/projects/${enc(pid)}/tours`, {
      query: { kind: p.kind, target: p.target || undefined, narrate: p.narrate || undefined },
      signal,
      timeoutMs: 180_000,
    }),
  explain: (pid: string, nodeId: string, useLlm = false, signal?: AbortSignal) =>
    request<Explanation>(`/projects/${enc(pid)}/explain`, {
      query: { node_id: nodeId, use_llm: useLlm || undefined },
      signal,
      timeoutMs: 150_000,
    }),
  search: (pid: string, p: { q: string; labels?: string[]; layers?: string[]; limit?: number; offset?: number }, signal?: AbortSignal) =>
    request<SearchResponse>(`/projects/${enc(pid)}/search`, { query: p, signal }),

  ask: (body: { project_id: string; question: string; focus_id?: string | null; focus_label?: string | null }, signal?: AbortSignal) =>
    request<Answer>("/ask", { method: "POST", json: body, signal, timeoutMs: 150_000 }),

  analyzeLocal: (body: { path: string; name?: string | null; dry_run: boolean } & WriteGuard, signal?: AbortSignal) =>
    request<AnalysisReport>("/projects/analyze", { method: "POST", json: body, signal, timeoutMs: 900_000 }),
  analyzeGithub: (body: { url: string; ref?: string | null; name?: string | null; dry_run: boolean } & WriteGuard, signal?: AbortSignal) =>
    request<AnalysisReport>("/projects/analyze/github", { method: "POST", json: body, signal, timeoutMs: 900_000 }),
  analyzeZip: (file: Blob, p: { filename: string; name?: string; dry_run: boolean; target_project_id?: string } & WriteGuard, signal?: AbortSignal) =>
    request<AnalysisReport>("/projects/analyze/zip", {
      method: "POST",
      query: p,
      body: file,
      headers: { "Content-Type": "application/zip" },
      signal,
      timeoutMs: 900_000,
    }),
};
