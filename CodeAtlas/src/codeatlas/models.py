"""Pydantic models shared by analysis, graph sync, API and UI."""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- extraction


class ScannedFile(BaseModel):
    path: str  # POSIX path relative to the project root
    abs_path: str
    size_bytes: int
    sha256: str


class SkippedFile(BaseModel):
    path: str
    reason: str


class ScanResult(BaseModel):
    root: str
    files: list[ScannedFile]
    skipped: list[SkippedFile] = []


class Symbol(BaseModel):
    kind: Literal["class", "function"]
    name: str
    qualname: str  # dotted within the file, e.g. "Cart.add" or "outer.inner"
    start_line: int  # 1-based, inclusive
    end_line: int
    parent_class: str | None = None  # qualname of the class when this is a method
    is_async: bool = False
    params: list[str] = []
    decorators: list[str] = []
    bases: list[str] = []  # raw base-class expressions (classes only)
    docstring: str | None = None


class ImportStmt(BaseModel):
    line: int
    kind: Literal["import", "from"]
    module: str  # dotted module text without leading dots ("" for `from . import x`)
    level: int = 0  # number of leading dots for relative imports
    names: list[tuple[str, str | None]] = []  # (name, alias); for `import a.b as c` -> [("a.b", "c")]


class CallSite(BaseModel):
    caller: str  # qualname of the enclosing function
    line: int
    callee: str  # final name being called ("fmt" in utils.fmt())
    receiver: str | None = None  # dotted receiver text ("utils", "self", "a.b"); None for bare calls


class FileAnalysis(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    loc: int
    docstring: str | None = None
    has_syntax_errors: bool = False
    symbols: list[Symbol] = []
    imports: list[ImportStmt] = []
    calls: list[CallSite] = []
    module_level_calls: int = 0


# ---------------------------------------------------------------- graph rows


class NodeRow(BaseModel):
    id: str
    props: dict[str, Any]


class RelRow(BaseModel):
    source: str
    target: str
    props: dict[str, Any] = {}


class GraphRows(BaseModel):
    """Everything one analysis run writes for a project, keyed by label / rel variant."""

    project: NodeRow
    nodes: dict[str, list[NodeRow]]  # label -> rows
    rels: dict[str, list[RelRow]]  # rel variant key (see graph.sync.REL_VARIANTS) -> rows

    def node_count(self) -> int:
        return 1 + sum(len(v) for v in self.nodes.values())

    def rel_count(self) -> int:
        return sum(len(v) for v in self.rels.values())


class ResolutionStats(BaseModel):
    imports_resolved: int = 0
    imports_external: int = 0
    imports_unresolved: int = 0
    calls_exact: int = 0
    calls_name_match: int = 0
    calls_builtin: int = 0
    calls_external: int = 0
    calls_ambiguous: int = 0
    calls_unresolved: int = 0
    inherits_resolved: int = 0
    inherits_unresolved: int = 0


class AnalysisReport(BaseModel):
    project_id: str
    project_name: str
    root_path: str
    dry_run: bool
    files_analyzed: int
    files_with_syntax_errors: list[str] = []
    skipped: list[SkippedFile] = []
    node_counts: dict[str, int] = {}
    rel_counts: dict[str, int] = {}
    resolution: ResolutionStats = ResolutionStats()
    run_id: str | None = None
    sync_status: str | None = None
    swept_nodes: int = 0
    swept_rels: int = 0
    duration_s: float = 0.0


# ---------------------------------------------------------------- API payloads


class GNode(BaseModel):
    id: str
    label: str
    caption: str
    props: dict[str, Any] = {}


class GEdge(BaseModel):
    id: str
    type: str
    source: str
    target: str
    props: dict[str, Any] = {}


class GraphPayload(BaseModel):
    """A graph built strictly from Neo4j query results."""

    nodes: list[GNode] = []
    edges: list[GEdge] = []
    cypher: list[str] = []
    params: dict[str, Any] = {}
    truncated: bool = False
    note: str | None = None


class AnalyzeRequest(BaseModel):
    path: str
    name: str | None = None
    dry_run: bool = False


class AskRequest(BaseModel):
    project_id: str
    question: str = Field(min_length=3, max_length=500)
    focus_id: str | None = None
    focus_label: str | None = None


class Answer(BaseModel):
    intent: str
    entity: GNode | None = None
    answer: str
    llm_used: bool
    facts: list[str] = []
    caveats: list[str] = []
    evidence: GraphPayload = GraphPayload()
