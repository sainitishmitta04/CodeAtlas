import { useEffect } from "react";
import { AppProvider, useApp } from "./context/AppContext";
import { AppShell, PAGE_TITLES } from "./components/layout/AppShell";
import { useRoute, buildHash } from "./lib/router";
import { EmptyState, ErrorState, Loading } from "./components/feedback/Feedback";
import { OverviewPage } from "./features/dashboard/OverviewPage";
import { GraphPage } from "./features/graph/GraphPage";
import { PathsPage } from "./features/graph/PathsPage";
import { ArchitecturePage } from "./features/architecture/ArchitecturePage";
import { ToursPage } from "./features/tours/ToursPage";
import { ExplainPage } from "./features/explain/ExplainPage";
import { SearchPage } from "./features/search/SearchPage";
import { AskPage } from "./features/qa/AskPage";
import { AnalyzePage } from "./features/projects/AnalyzePage";
import { DiagnosticsPage } from "./features/settings/DiagnosticsPage";

function Routes() {
  const { route, pid, projects, projectsError, projectsLoading, project, reloadProjects } = useApp();

  useEffect(() => {
    document.title = `${PAGE_TITLES[route.page]}${project ? ` · ${project.name}` : ""} — CodeAtlas`;
  }, [route.page, project]);

  if (route.page === "analyze") return <AnalyzePage />;
  if (route.page === "diagnostics") return <DiagnosticsPage />;

  if (projectsLoading && !projects) return <Loading label="Loading projects from Neo4j…" />;
  if (projectsError && !projects) return <ErrorState error={projectsError} onRetry={reloadProjects} title="Could not load projects" />;
  if (!pid || !projects?.length) {
    return (
      <EmptyState
        title="No CodeAtlas projects in Neo4j yet"
        icon="◇"
        action={<a className="btn btn--primary" href={buildHash("analyze", null)}>Analyze a repository</a>}
      >
        Analyze a local directory, a public GitHub repository or a ZIP archive to build its code graph.
      </EmptyState>
    );
  }
  if (route.pid && !project) {
    return (
      <EmptyState title="Unknown project" icon="?" action={<a className="btn" href={buildHash("overview", projects[0].id)}>Open {projects[0].name}</a>}>
        <span className="mono">{route.pid}</span> is not a CodeAtlas project in this database.
      </EmptyState>
    );
  }

  switch (route.page) {
    case "graph": return <GraphPage />;
    case "paths": return <PathsPage />;
    case "architecture": return <ArchitecturePage />;
    case "tours": return <ToursPage />;
    case "explain": return <ExplainPage />;
    case "search": return <SearchPage />;
    case "ask": return <AskPage />;
    default: return <OverviewPage />;
  }
}

export default function App() {
  const route = useRoute();
  return (
    <AppProvider route={route}>
      <AppShell>
        <div className="page" key={`${route.page}|${route.pid ?? ""}`}>
          <Routes />
        </div>
      </AppShell>
    </AppProvider>
  );
}
