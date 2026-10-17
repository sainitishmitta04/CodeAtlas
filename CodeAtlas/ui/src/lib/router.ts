// Minimal hash router: #/p/<project-id>/<page>?<params>  or  #/<page>?<params> for pages
// that do not need a project. Hash routing works with `vite preview` and any static server.

import { useEffect, useState } from "react";

export type Page =
  | "overview" | "graph" | "paths" | "architecture" | "tours" | "explain" | "search" | "ask"
  | "analyze" | "diagnostics";

export const PROJECT_PAGES: Page[] = ["overview", "graph", "paths", "architecture", "tours", "explain", "search", "ask"];
const ALL_PAGES = new Set<Page>([...PROJECT_PAGES, "analyze", "diagnostics"]);

export interface Route {
  page: Page;
  pid: string | null;
  params: Record<string, string>;
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  const [pathPart, queryPart = ""] = raw.split("?", 2);
  const segs = pathPart.split("/").filter(Boolean).map((s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  });
  const params = Object.fromEntries(new URLSearchParams(queryPart));
  if (segs[0] === "p" && segs[1]) {
    const page = (segs[2] ?? "overview") as Page;
    return { page: ALL_PAGES.has(page) ? page : "overview", pid: segs[1], params };
  }
  const page = segs[0] as Page | undefined;
  return { page: page && ALL_PAGES.has(page) ? page : "overview", pid: null, params };
}

export function buildHash(page: Page, pid: string | null, params: Record<string, string | undefined | null> = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") qs.set(k, v);
  const q = qs.toString();
  const path = pid && PROJECT_PAGES.includes(page) ? `/p/${encodeURIComponent(pid)}/${page}` : `/${page}`;
  return `#${path}${q ? `?${q}` : ""}`;
}

export function navigate(page: Page, pid: string | null, params: Record<string, string | undefined | null> = {}, replace = false): void {
  const hash = buildHash(page, pid, params);
  if (hash === window.location.hash) return;
  if (replace) {
    history.replaceState(null, "", hash);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = hash;
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
