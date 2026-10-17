import { useEffect, useState, type ReactNode } from "react";
import { useApp } from "../../context/AppContext";
import { buildHash, type Page } from "../../lib/router";
import { fmtWhen } from "../../lib/format";
import { Spinner } from "../feedback/Feedback";

const NAV: { page: Page; label: string; glyph: string; group: "explore" | "intel" | "work" }[] = [
  { page: "overview", label: "Overview", glyph: "◰", group: "explore" },
  { page: "graph", label: "Graph", glyph: "⌘", group: "explore" },
  { page: "paths", label: "Paths & Impact", glyph: "⇢", group: "explore" },
  { page: "architecture", label: "Architecture", glyph: "▤", group: "intel" },
  { page: "tours", label: "Tours", glyph: "◎", group: "intel" },
  { page: "explain", label: "Explain", glyph: "¶", group: "intel" },
  { page: "search", label: "Search", glyph: "⌕", group: "intel" },
  { page: "ask", label: "Q&A", glyph: "?", group: "intel" },
  { page: "analyze", label: "Analyze repository", glyph: "+", group: "work" },
  { page: "diagnostics", label: "Diagnostics", glyph: "◇", group: "work" },
];

export const PAGE_TITLES: Record<Page, string> = Object.fromEntries(NAV.map((n) => [n.page, n.label])) as Record<Page, string>;

function Status({ ok, label, detail }: { ok: boolean | null; label: string; detail: string }) {
  return (
    <div className="status" title={detail}>
      <span className={`status__dot ${ok === null ? "is-unknown" : ok ? "is-ok" : "is-bad"}`} aria-hidden="true" />
      <span className="status__label">{label}</span>
      <span className="status__detail">{detail}</span>
    </div>
  );
}

function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
      <circle cx="16" cy="16" r="12" fill="none" stroke="var(--sage)" strokeOpacity=".45" strokeWidth="1.2" />
      <circle cx="16" cy="16" r="6.5" fill="none" stroke="var(--line-3)" strokeWidth="1" strokeDasharray="2 2.5" />
      <path d="M8.5 22 16 7.5 23.5 22" fill="none" stroke="var(--copper)" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M11.4 17h9.2" stroke="var(--copper)" strokeWidth="1.4" />
      <circle cx="16" cy="7.5" r="2.1" fill="var(--paper)" />
      <circle cx="8.5" cy="22" r="2.1" fill="var(--sage)" />
      <circle cx="23.5" cy="22" r="2.1" fill="var(--sage)" />
    </svg>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { route, projects, projectsLoading, project, pid, health, healthError, selectProject, refresh, focus } = useApp();
  const [navOpen, setNavOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => setNavOpen(false), [route.page, route.pid]);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  const llm = health?.llm;
  const last = llm?.last_call;
  const llmOk = !llm ? null : !llm.configured ? false : last?.state === "ok" ? true : last?.state === "failed" ? false : null;
  const llmDetail = !llm
    ? "unknown"
    : !llm.configured
      ? "not configured"
      : last?.state === "ok"
        ? "last call OK"
        : last?.state === "failed"
          ? `last call failed: ${last.code ?? "error"}${last.provider_status ? ` (HTTP ${last.provider_status})` : ""}`
          : "configured, not verified yet";

  const groups: { key: string; title: string }[] = [
    { key: "explore", title: "Explore" },
    { key: "intel", title: "Intelligence" },
    { key: "work", title: "Workspace" },
  ];

  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="sidebar" aria-label="Primary">
        <div className="sidebar__brand">
          <Logo />
          <div>
            <div className="brand__name">CodeAtlas</div>
            <div className="brand__tag">architecture workstation</div>
          </div>
        </div>

        <div className="sidebar__project">
          <label className="field-label" htmlFor="project-select">Project</label>
          {projectsLoading && !projects ? (
            <div className="sidebar__loading"><Spinner size={14} /> Loading projects…</div>
          ) : projects && projects.length ? (
            <div className="select-wrap">
              <select id="project-select" className="select" value={pid ?? ""} onChange={(e) => selectProject(e.target.value)}>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          ) : (
            <div className="sidebar__hint">{projects ? "No projects in Neo4j yet." : "Projects unavailable."}</div>
          )}
          {project && (
            <div className="sidebar__pmeta mono" title={project.id}>
              {project.id}
            </div>
          )}
        </div>

        <nav className="nav">
          {groups.map((g) => (
            <div key={g.key} className="nav__group">
              <div className="nav__title">{g.title}</div>
              <ul>
                {NAV.filter((n) => n.group === g.key).map((n) => {
                  const needsProject = n.group !== "work";
                  const disabled = needsProject && !pid;
                  const active = route.page === n.page;
                  return (
                    <li key={n.page}>
                      <a
                        className={`nav__link${active ? " is-active" : ""}${disabled ? " is-disabled" : ""}`}
                        href={disabled ? undefined : buildHash(n.page, pid)}
                        aria-current={active ? "page" : undefined}
                        aria-disabled={disabled || undefined}
                      >
                        <span className="nav__glyph" aria-hidden="true">{n.glyph}</span>
                        {n.label}
                      </a>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="sidebar__status" aria-label="Service status">
          <Status
            ok={healthError ? false : health ? health.neo4j.ok : null}
            label="Neo4j"
            detail={healthError ? "API unreachable" : health ? (health.neo4j.ok ? "connected" : health.neo4j.error ?? "unavailable") : "checking…"}
          />
          <Status ok={healthError ? false : llmOk} label={`LLM${health ? ` · ${health.model}` : ""}`} detail={healthError ? "API unreachable" : llmDetail} />
        </div>
      </aside>
      <div className="scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />

      <div className="workspace">
        <header className={`topbar${scrolled ? " is-scrolled" : ""}`}>
          <button className="icon-btn topbar__menu" onClick={() => setNavOpen((o) => !o)} aria-label="Toggle navigation" aria-expanded={navOpen}>
            ☰
          </button>
          <nav className="crumbs" aria-label="Breadcrumb">
            <span className="crumbs__root">CodeAtlas</span>
            {project && route.page !== "analyze" && route.page !== "diagnostics" && (
              <>
                <span className="crumbs__sep" aria-hidden="true">/</span>
                <span className="crumbs__proj">{project.name}</span>
              </>
            )}
            <span className="crumbs__sep" aria-hidden="true">/</span>
            <span className="crumbs__page" aria-current="page">{PAGE_TITLES[route.page]}</span>
          </nav>
          <div className="topbar__meta">
            {focus && (
              <span className="chip chip--focus" title={`Focused graph node: ${focus.caption}`}>
                <i style={{ background: "var(--copper)" }} /> focus <b className="mono">{focus.caption}</b>
              </span>
            )}
            {project && (
              <span
                className={`chip${project.sync_status && project.sync_status !== "complete" ? " chip--warn" : ""}`}
                title={`Sync status: ${project.sync_status ?? "unknown"} · synced ${project.synced_at ?? "—"}`}
              >
                <i /> {project.sync_status === "complete" ? "synced" : project.sync_status ?? "status unknown"} · {fmtWhen(project.synced_at)}
              </span>
            )}
            <button className="btn btn--ghost btn--sm" onClick={refresh} title="Re-read projects, health and the current page from the API (read-only)">
              ↻ Refresh
            </button>
          </div>
        </header>
        <main id="main" className="main" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
