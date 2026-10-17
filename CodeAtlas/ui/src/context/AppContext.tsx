import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import type { ArchitectureReport, GNode, Health, ProjectSummary } from "../lib/types";
import { navigate, type Route } from "../lib/router";

interface AppState {
  route: Route;
  projects: ProjectSummary[] | null;
  projectsError: unknown;
  projectsLoading: boolean;
  project: ProjectSummary | null;
  pid: string | null;
  health: Health | null;
  healthError: unknown;
  /** Bumped by the top-bar refresh: pages include it in their request dependencies. */
  refreshKey: number;
  refresh: () => void;
  reloadProjects: () => void;
  reloadHealth: () => void;
  /** Node selected in the graph, offered as a focus elsewhere (Ask, Paths, Explain). */
  focus: GNode | null;
  setFocus: (n: GNode | null) => void;
  selectProject: (pid: string) => void;
}

const Ctx = createContext<AppState | null>(null);
const LAST_PROJECT = "codeatlas.lastProject";

export function AppProvider({ route, children }: { route: Route; children: ReactNode }) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [focus, setFocus] = useState<GNode | null>(null);
  const projects = useApi((s) => api.projects(s), [refreshKey]);
  const health = useApi((s) => api.health(s), [refreshKey]);

  // Resolve the active project: route > last used > first available.
  const pid = useMemo(() => {
    if (route.pid) return route.pid;
    const list = projects.data;
    if (!list?.length) return null;
    const last = localStorage.getItem(LAST_PROJECT);
    return list.find((p) => p.id === last)?.id ?? list[0].id;
  }, [route.pid, projects.data]);

  useEffect(() => {
    if (pid) localStorage.setItem(LAST_PROJECT, pid);
    setFocus((f) => (f && f.props.project_id !== pid && f.id !== pid ? null : f));
  }, [pid]);

  const project = projects.data?.find((p) => p.id === pid) ?? null;

  const selectProject = useCallback(
    (next: string) => navigate(route.page === "analyze" || route.page === "diagnostics" ? "overview" : route.page, next),
    [route.page],
  );

  const value: AppState = {
    route,
    projects: projects.data,
    projectsError: projects.error,
    projectsLoading: projects.loading,
    project,
    pid,
    health: health.data,
    healthError: health.error,
    refreshKey,
    refresh: useCallback(() => {
      archCache.clear();
      setRefreshKey((k) => k + 1);
    }, []),
    reloadProjects: projects.reload,
    reloadHealth: health.reload,
    focus,
    setFocus,
    selectProject,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

// The backend caches architecture per sync run; this avoids re-requesting it on every page switch.
const archCache = new Map<string, Promise<ArchitectureReport>>();

export function useArchitecture() {
  const { pid, project, refreshKey } = useApp();
  return useApi(
    pid
      ? (signal) => {
          const key = `${pid}|${project?.synced_at ?? ""}`;
          let p = archCache.get(key);
          if (!p) {
            p = api.architecture(pid);
            archCache.set(key, p);
            p.catch(() => archCache.delete(key));
          }
          return abortable(p, signal);
        }
      : null,
    [pid, project?.synced_at, refreshKey],
  );
}

function abortable<T>(p: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(new DOMException("aborted", "AbortError"));
    if (signal.aborted) return onAbort();
    signal.addEventListener("abort", onAbort, { once: true });
    p.then(resolve, reject).finally(() => signal.removeEventListener("abort", onAbort));
  });
}
