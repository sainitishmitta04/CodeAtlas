// Response and request shapes of the CodeAtlas FastAPI backend (src/codeatlas/api/main.py,
// models.py, graph/queries.py, intel/*.py). Field names match the backend exactly.

export type NodeLabel = "Project" | "File" | "Module" | "Class" | "Function";
export type RelType = "CONTAINS" | "DEFINES" | "HAS_METHOD" | "IMPORTS" | "INHERITS" | "CALLS";

export const LABELS: NodeLabel[] = ["Project", "File", "Module", "Class", "Function"];
export const REL_TYPES: RelType[] = ["CONTAINS", "DEFINES", "HAS_METHOD", "IMPORTS", "INHERITS", "CALLS"];

export type Props = Record<string, unknown>;

// ------------------------------------------------------------------ health / projects

export interface LlmLastCall {
  state: "unknown" | "ok" | "failed" | string;
  at?: string;
  code?: string;
  provider_status?: number | null;
  request_id?: string | null;
}

export interface Health {
  neo4j: { ok: boolean; error?: string };
  llm_configured: boolean;
  model: string;
  llm: {
    configured: boolean;
    model: string;
    base_url_host: string;
    base_url_valid: boolean;
    last_call: LlmLastCall;
  };
  config: { env_file_found: boolean; shell_vars_ignored_in_favour_of_env_file: string[] };
  max_upload_bytes: number;
}

export interface ProjectSummary {
  id: string;
  name: string;
  root_path: string | null;
  sync_status: string | null;
  synced_at: string | null;
  file_count: number | null;
  sync_error: string | null;
  source_kind: "local" | "github" | "zip" | null;
  source_label: string | null;
}

/** GET /projects/{pid}: every stored Project property (minus lock bookkeeping). */
export type ProjectDetail = Props & { id: string; name?: string };

export interface ProjectStats {
  nodes: Record<string, number>;
  relationships: Record<string, number>;
  call_confidence: { confidence: string | null; edges: number; call_sites: number | null }[];
  import_resolution: { resolution: string | null; edges: number; statements: number | null }[];
  top_imported_files: { path: string; importers: number; symbols: number; docstring: string | null }[];
  top_called_functions: { function: string; file: string; callers: number }[];
  cypher: string[];
}

export type Integrity = Record<string, number>;
export interface FileEntry { id: string; path: string }
export interface FunctionEntry { id: string; qualname: string; file: string }

// ------------------------------------------------------------------ graph payloads

export interface GNode { id: string; label: NodeLabel | string; caption: string; props: Props }
export interface GEdge { id: string; type: RelType | string; source: string; target: string; props: Props }

export interface OverviewTotals {
  nodes: number;
  relationships: number;
  nodes_by_label: Record<string, number>;
  selected_nodes: number;
  selected_relationships: number;
  hidden_relationships: number;
}

export interface GraphPayload {
  nodes: GNode[];
  edges: GEdge[];
  cypher: string[];
  params: Props;
  truncated: boolean;
  note: string | null;
  next_cursor: string | null;
  complete: boolean | null;
  page_nodes: number | null;
  totals: OverviewTotals | null;
}

export interface ImpactRow { file: string; hops: number; function?: string; class?: string; confidences?: (string | null)[] }
export interface ImpactResult {
  target: { label: string; id: string };
  depth: number;
  defined_symbols: string[];
  importing_files: ImpactRow[];
  calling_functions: ImpactRow[];
  subclasses: ImpactRow[];
  coverage_notes: string[];
  affected_files: string[];
  graph: GraphPayload;
}

// ------------------------------------------------------------------ ingestion

export interface SkippedFile { path: string; reason: string }

export interface SourceTarget {
  kind: "local" | "github" | "zip";
  label: string;
  identity: string;
  project_id: string;
  project_name: string;
  ref: string | null;
  ref_is_default: boolean | null;
  fingerprint: string | null;
  exists: boolean | null;
  existing_name: string | null;
  existing_status: string | null;
  existing_synced_at: string | null;
  notes: string[];
}

export interface AnalysisReport {
  project_id: string;
  project_name: string;
  root_path: string;
  dry_run: boolean;
  files_analyzed: number;
  files_with_syntax_errors: string[];
  skipped: SkippedFile[];
  node_counts: Record<string, number>;
  rel_counts: Record<string, number>;
  resolution: Record<string, number>;
  run_id: string | null;
  sync_status: string | null;
  swept_nodes: number;
  swept_rels: number;
  duration_s: number;
  source: SourceTarget | null;
}

export type WriteGuard = { expected_project_id?: string; confirm_update?: boolean };

// ------------------------------------------------------------------ Q&A

export interface Answer {
  intent: string;
  entity: GNode | null;
  answer: string;
  llm_used: boolean;
  kind: "llm" | "clarification";
  facts: string[];
  caveats: string[];
  evidence: GraphPayload;
}

/** `detail` of a non-2xx /ask (and any other LLM failure): LLMError.to_dict() + extras. */
export interface LlmErrorDetail {
  code: string;
  message: string;
  hint?: string | null;
  provider_status?: number | null;
  provider_message?: string | null;
  request_id?: string | null;
  retryable?: boolean;
  attempts?: number;
  component?: string;
  graph_context?: Answer;
}

// ------------------------------------------------------------------ Phase 2: architecture

export type Confidence = "high" | "medium" | "low" | "none";

export interface Evidence { layer: string; kind: string; detail: string; weight: number }
export interface FileLayer {
  file_id: string;
  path: string;
  component: string;
  layer: string;
  confidence: Confidence;
  scores: Record<string, number>;
  evidence: Evidence[];
}
export interface ComponentDep { component: string; imports: number; calls: number }
export interface Component {
  name: string;
  layer: string;
  confidence: Confidence;
  ambiguous_between: string[];
  scores: Record<string, number>;
  evidence: string[];
  files: string[];
  symbol_count: number;
  loc: number;
  depends_on: ComponentDep[];
  used_by: ComponentDep[];
  entry_points: string[];
}
export interface LayerSummary { layer: string; title: string; components: string[]; file_count: number }
export interface LayerEdge { source: string; target: string; imports: number; possible_violation: boolean }
export interface Violation {
  source_component: string;
  target_component: string;
  source_layer: string;
  target_layer: string;
  imports: number;
  example: string;
}
export interface ArchitectureReport {
  project_id: string;
  layers: LayerSummary[];
  components: Component[];
  files: FileLayer[];
  layer_edges: LayerEdge[];
  violations: Violation[];
  component_cycles: string[][];
  file_cycles: string[][];
  entry_points: string[];
  notes: string[];
}

// ------------------------------------------------------------------ Phase 2: tours

export type TourKind = "overview" | "entry" | "component" | "execution" | "dependency" | "impact";
export const TOUR_KINDS: TourKind[] = ["overview", "entry", "component", "execution", "dependency", "impact"];

export interface Location { file_path?: string; start_line?: number; end_line?: number }

export interface TourStep {
  index: number;
  title: string;
  explanation: string;
  kind: "layer" | "component" | "file" | "symbol" | "module" | "summary";
  node_id: string | null;
  node_label: string | null;
  layer: string | null;
  component: string | null;
  location: Location | null;
  evidence: string[];
  observed: boolean;
}

export interface Tour {
  kind: TourKind;
  title: string;
  purpose: string;
  target: string | null;
  steps: TourStep[];
  notes: string[];
  truncated: boolean;
  narrative: string | null;
  llm_error: LlmErrorDetail | null;
}

// ------------------------------------------------------------------ Phase 2: explain

export interface Ref {
  node_id: string;
  label: string;
  display: string;
  location: Location | null;
  lines: number[];
  confidence: string | null;
  layer: string | null;
}

export interface SourceExcerpt { file_path: string; start_line: number; end_line: number; text: string; verified: boolean }

export interface SymbolFacts {
  node_id: string;
  label: string;
  kind: string;
  name: string;
  qualname: string | null;
  location: Location | null;
  layer: string | null;
  component: string | null;
  signature: string | null;
  params: string[] | null;
  decorators: string[];
  bases: string[];
  docstring: string | null;
  belongs_to: Ref[];
  calls: Ref[];
  callers: Ref[];
  methods: Ref[];
  parents: Ref[];
  subclasses: Ref[];
  defines: Ref[];
  imports: Ref[];
  imported_by: Ref[];
  external_calls: string[];
  unresolved_calls: string[];
  side_effect_hints: string[];
  counts: Record<string, number>;
  unknowns: string[];
  source_excerpt: SourceExcerpt | null;
}

export interface Explanation {
  facts: SymbolFacts;
  interpretation: string | null;
  llm_used: boolean;
  llm_error: LlmErrorDetail | null;
}

// ------------------------------------------------------------------ Phase 2: search

export interface SearchResult {
  node_id: string;
  label: string;
  name: string;
  display: string;
  location: Location | null;
  layer: string | null;
  component: string | null;
  score: number;
  matched: string[];
  summary: string | null;
}

export interface SearchResponse {
  query: string;
  terms: string[];
  expanded_terms: Record<string, string[]>;
  mode: string;
  semantic: boolean;
  note: string;
  total: number;
  offset: number;
  limit: number;
  results: SearchResult[];
}
