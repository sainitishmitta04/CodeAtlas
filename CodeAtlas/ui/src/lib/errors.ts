// Errors returned by the API client. The backend's error body is
// {"detail": string | object | validation-list, "component": "neo4j" | "llm" | "github" | "source" | "api"}.

export type Component = "neo4j" | "llm" | "github" | "source" | "api" | "network" | "timeout" | string;

export class ApiError extends Error {
  /** HTTP status; 0 when the backend could not be reached. */
  readonly status: number;
  /** Which dependency failed, as reported by the backend (or "network"/"timeout"). */
  readonly component: Component | null;
  /** The raw `detail` value (string, object or validation list). */
  readonly detail: unknown;

  constructor(status: number, message: string, component: Component | null, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.component = component;
    this.detail = detail;
  }

  /** The detail as an object when the backend sent a structured error (LLM, confirmation). */
  get detailObject(): Record<string, unknown> | null {
    return this.detail && typeof this.detail === "object" && !Array.isArray(this.detail)
      ? (this.detail as Record<string, unknown>)
      : null;
  }

  get code(): string | null {
    const c = this.detailObject?.code;
    return typeof c === "string" ? c : null;
  }
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** Human-readable text for FastAPI's `detail` in any of its shapes. */
export function detailMessage(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.filter((x) => x !== "query" && x !== "body").join(".")
            : "";
          return loc ? `${loc}: ${String((d as { msg: unknown }).msg)}` : String((d as { msg: unknown }).msg);
        }
        return String(d);
      })
      .join("; ");
  }
  if (typeof detail === "object") {
    const msg = (detail as Record<string, unknown>).message;
    if (typeof msg === "string") return msg;
    return JSON.stringify(detail);
  }
  return String(detail);
}

const COMPONENT_NAMES: Record<string, string> = {
  neo4j: "Neo4j",
  llm: "LLM",
  github: "GitHub",
  source: "Repository source",
  api: "API",
  network: "Backend",
  timeout: "Backend",
};

export function componentName(component: string | null): string {
  return component ? COMPONENT_NAMES[component] ?? component : "API";
}

/** One line suitable for an error banner. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return err.message;
    return `${componentName(err.component)} error (${err.status}): ${err.message}`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
