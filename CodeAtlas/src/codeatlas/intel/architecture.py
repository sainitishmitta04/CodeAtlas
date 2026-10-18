"""Architectural layers inferred from the code graph.

Unit of analysis: a *component*. For small projects (<= FILE_COMPONENT_LIMIT files) each
file (module) is a component; otherwise a component is a source directory (at most two
levels below the source root, leading `src/`/`lib/` stripped; root files form `(root)`).

Each file collects weighted evidence for candidate layers. Strong evidence comes from what
the code *does* (framework imports, decorators, base classes, test frameworks); names and
paths are weak evidence; graph position (nothing imports it / everything imports it) is
weak supporting evidence. A component's layer is the best-supported layer of its files, or
"unclassified" when the evidence is missing or too close to call (the candidates are shown).

Everything observed (imports, decorators, counts) is reported as evidence; the layer itself
is always an inference, with a confidence.
"""

import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel

from codeatlas.intel.snapshot import Snapshot

LAYERS = ["presentation", "api", "business", "domain", "data", "infrastructure", "utilities",
          "tests"]
UNCLASSIFIED = "unclassified"
LAYER_TITLES = {
    "presentation": "Presentation / UI", "api": "API & entry points",
    "business": "Business logic / services", "domain": "Domain models",
    "data": "Data access & persistence", "infrastructure": "Infrastructure & integrations",
    "utilities": "Shared utilities", "tests": "Tests", UNCLASSIFIED: "Unclassified",
}
# Conventional dependency direction: a layer may depend on layers with a higher rank.
# Tests, utilities and unclassified components are never reported as violations.
RANK = {"presentation": 0, "api": 1, "business": 2, "data": 3, "infrastructure": 3,
        "domain": 4, "utilities": 5}

# Weak evidence: directory / file name tokens.
NAME_HINTS = {
    "presentation": {"ui", "views", "view", "templates", "pages", "frontend", "web", "gui",
                     "streamlit", "components", "widgets"},
    "api": {"api", "routes", "router", "routers", "endpoints", "handlers", "controllers",
            "cli", "main", "app", "server", "__main__", "commands", "wsgi", "asgi"},
    "business": {"services", "service", "usecases", "use_cases", "core", "logic", "business",
                 "domain_services", "workflows", "pipeline", "engine"},
    "domain": {"models", "model", "schemas", "schema", "entities", "entity", "domain",
               "types", "dto", "dtos"},
    "data": {"db", "database", "repository", "repositories", "repo", "dao", "persistence",
             "storage", "store", "migrations", "orm", "sql", "queries", "crud"},
    "infrastructure": {"clients", "client", "integrations", "adapters", "external", "infra",
                       "infrastructure", "config", "settings", "gateway", "gateways", "llm",
                       "http", "messaging", "queue", "cache"},
    "utilities": {"utils", "util", "helpers", "helper", "common", "shared", "lib", "misc",
                  "tools"},
    "tests": {"tests", "test", "testing", "conftest", "fixtures"},
}
# Strong evidence: external modules imported (top-level package name).
MODULE_HINTS = {
    "presentation": {"streamlit", "tkinter", "jinja2", "dash", "gradio", "flet", "kivy", "pyqt5",
                     "pyside6", "st_link_analysis"},
    "api": {"fastapi", "flask", "starlette", "django.urls", "click", "typer", "argparse",
            "aiohttp.web", "falcon", "sanic", "tornado"},
    "data": {"sqlalchemy", "psycopg2", "psycopg", "pymongo", "neo4j", "sqlite3", "redis",
             "peewee", "django.db", "motor", "asyncpg", "mysql", "elasticsearch", "boto3.dynamodb"},
    "infrastructure": {"requests", "httpx", "boto3", "openai", "google", "aiohttp", "grpc",
                       "kafka", "pika", "smtplib", "socket", "urllib", "paramiko", "anthropic",
                       "dotenv", "pydantic_settings", "logging.config"},
    "tests": {"pytest", "unittest", "mock", "hypothesis"},
}
# Weak module evidence: common in every layer, so it only tips close calls.
WEAK_MODULE_HINTS = {"domain": {"pydantic", "dataclasses", "attrs", "enum", "marshmallow"}}
ENTRY_NAMES = {"main", "app", "cli", "__main__", "server", "manage", "wsgi", "asgi", "run"}
FILE_COMPONENT_LIMIT = 40
DECORATOR_HINTS = [
    (re.compile(r"(^|\.)(get|post|put|patch|delete|route|websocket|api_route)$"), "api",
     "HTTP route decorator"),
    (re.compile(r"(^|\.)(command|group|callback)$"), "api", "CLI command decorator"),
    (re.compile(r"(^|\.)(fixture|mark\..*|parametrize)$"), "tests", "test decorator"),
    (re.compile(r"(^|\.)dataclass$"), "domain", "@dataclass (weak)"),
    (re.compile(r"(^|\.)(cache_data|cache_resource|fragment|dialog)$"), "presentation",
     "Streamlit decorator"),
]
BASE_HINTS = [
    (re.compile(r"(^|\.)(BaseModel|BaseSettings)$"), "domain", "Pydantic model"),
    (re.compile(r"(^|\.)(Model|Base|DeclarativeBase)$"), "data", "ORM model base"),
    (re.compile(r"(^|\.)(Enum|IntEnum|StrEnum|TypedDict|NamedTuple)$"), "domain",
     "enum / typed record"),
    (re.compile(r"(^|\.)TestCase$"), "tests", "unittest.TestCase"),
    (re.compile(r"(^|\.)(Exception|Error|RuntimeError|ValueError|LookupError)$"), "domain",
     "exception type"),
]
WEAK_BASES = {"exception type"}
WEIGHT = {"module": 3.0, "decorator": 3.0, "base": 2.0, "test_name": 3.0, "name": 1.5,
          "graph": 1.0, "position": 2.0, "weak": 0.5}
MIN_SCORE = 1.5  # a name hint alone is enough for a *low*-confidence classification


# ------------------------------------------------------------------ result models

class Evidence(BaseModel):
    layer: str
    kind: str  # module | decorator | base | test_name | name | graph
    detail: str
    weight: float


class FileLayer(BaseModel):
    file_id: str
    path: str
    component: str
    layer: str
    confidence: str  # high | medium | low | none
    scores: dict[str, float] = {}
    evidence: list[Evidence] = []


class ComponentDep(BaseModel):
    component: str
    imports: int  # number of file-level IMPORTS relationships
    calls: int = 0  # number of CALLS relationships between their symbols


class Component(BaseModel):
    name: str
    layer: str
    confidence: str
    ambiguous_between: list[str] = []
    scores: dict[str, float] = {}
    evidence: list[str] = []  # top observed signals, human readable
    files: list[str] = []
    symbol_count: int = 0
    loc: int = 0
    depends_on: list[ComponentDep] = []
    used_by: list[ComponentDep] = []
    entry_points: list[str] = []


class LayerSummary(BaseModel):
    layer: str
    title: str
    components: list[str]
    file_count: int


class LayerEdge(BaseModel):
    source: str
    target: str
    imports: int
    possible_violation: bool = False


class Violation(BaseModel):
    source_component: str
    target_component: str
    source_layer: str
    target_layer: str
    imports: int
    example: str  # an observed file -> file import


class ArchitectureReport(BaseModel):
    project_id: str
    layers: list[LayerSummary]
    components: list[Component]
    files: list[FileLayer]
    layer_edges: list[LayerEdge]
    violations: list[Violation]
    component_cycles: list[list[str]]
    file_cycles: list[list[str]]
    entry_points: list[str]  # file paths
    notes: list[str]


# ------------------------------------------------------------------ helpers

def component_of(path: str, per_file: bool = False) -> str:
    if per_file:
        return posixpath.splitext(path)[0]
    parts = path.split("/")[:-1]
    while parts and parts[0] in ("src", "lib", "source"):
        parts = parts[1:]
    return "/".join(parts[:2]) or "(root)"


def _tokens(text: str) -> set[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1_\2", text or "")
    return {t for t in re.split(r"[^a-zA-Z0-9]+", text.lower()) if t}


def _module_matches(module: str, hints: set[str]) -> str | None:
    m = module.lower()
    for h in hints:
        if m == h or m.startswith(h + "."):
            return h
    return None


@dataclass
class _Acc:
    scores: Counter = field(default_factory=Counter)
    evidence: list[Evidence] = field(default_factory=list)

    def add(self, layer: str, kind: str, detail: str) -> None:
        w = WEIGHT[kind]
        self.scores[layer] += w
        self.evidence.append(Evidence(layer=layer, kind=kind, detail=detail, weight=w))


def _decide(scores: Counter, min_score: float = MIN_SCORE) -> tuple[str, str, list[str]]:
    """(layer, confidence, ambiguous_between)."""
    ranked = scores.most_common()
    if not ranked or ranked[0][1] < min_score:
        return UNCLASSIFIED, "none", [layer for layer, _ in ranked[:3]]
    top, top_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score - second < 1.0:  # too close to call
        return UNCLASSIFIED, "low", [layer for layer, s in ranked if s >= second][:3]
    confidence = ("high" if top_score >= 5 and top_score >= 2 * max(second, 0.5)
                  else "medium" if top_score >= 3 else "low")
    return top, confidence, []


def _symbols_of_file(snap: Snapshot, fid: str) -> list[dict]:
    out = []
    for e in snap.out_edges(fid, "DEFINES"):
        sym = snap.nodes.get(e.target)
        if sym:
            out.append(sym)
            out += [snap.nodes[m.target] for m in snap.out_edges(e.target, "HAS_METHOD")
                    if m.target in snap.nodes]
    return out


def _root_packages(paths: list[str]) -> set[str]:
    """Top-level directories that hold most of the project: the project's own package name
    (e.g. `app/`, `codeatlas/`) says nothing about layers, so it is not used as evidence."""
    tops = Counter(_strip_prefix(p.split("/"))[0] for p in paths
                   if len(_strip_prefix(p.split("/"))) > 1)
    return {t for t, n in tops.items() if n > len(paths) / 2}


def _strip_prefix(parts: list[str]) -> list[str]:
    while len(parts) > 1 and parts[0] in ("src", "lib", "source"):
        parts = parts[1:]
    return parts


def _file_evidence(snap: Snapshot, f: dict, importers: int, internal_imports: int,
                   root_packages: set[str] = frozenset()) -> _Acc:
    acc = _Acc()
    path = f.get("path", "")
    parts = _strip_prefix(posixpath.splitext(path)[0].split("/"))
    if len(parts) > 1 and parts[0] in root_packages:
        parts = parts[1:]
    name_tokens = _tokens("/".join(parts))
    for layer, hints in NAME_HINTS.items():
        hit = sorted(name_tokens & hints)
        if hit:
            acc.add(layer, "name", f"path contains '{hit[0]}'")
    base = posixpath.basename(path)
    js_test = base.rsplit(".", 1)[-1] in ("js", "jsx", "ts", "tsx", "mjs", "cjs") and (
        ".test." in base or ".spec." in base or "/__tests__/" in f"/{path}")
    if base.startswith("test_") or path.endswith("_test.py") or js_test:
        acc.add("tests", "test_name", "test file name")

    for e in snap.out_edges(f["id"], "IMPORTS"):
        target = snap.nodes.get(e.target, {})
        if target.get("label") != "Module":
            continue
        for kind, table in (("module", MODULE_HINTS), ("weak", WEAK_MODULE_HINTS)):
            layer = next((lyr for lyr, hints in table.items()
                          if _module_matches(target.get("name", ""), hints)), None)
            if layer:
                acc.add(layer, kind, f"imports {target.get('name')}")
                break

    seen = set()
    for sym in _symbols_of_file(snap, f["id"]):
        for dec in sym.get("decorators") or []:
            for rx, layer, what in DECORATOR_HINTS:
                if rx.search(dec.split("(")[0].lstrip("@")) and (layer, what) not in seen:
                    seen.add((layer, what))
                    acc.add(layer, "weak" if "(weak)" in what else "decorator",
                            f"{what} on {sym.get('qualname')}")
        for base in sym.get("bases") or []:
            for rx, layer, what in BASE_HINTS:
                if rx.search(base) and (layer, what) not in seen:
                    seen.add((layer, what))
                    acc.add(layer, "weak" if what in WEAK_BASES else "base",
                            f"{sym.get('name')} subclasses {base} ({what})")
        if sym.get("label") == "Function" and str(sym.get("name", "")).startswith("test_") \
                and ("tests", "test fn") not in seen:
            seen.add(("tests", "test fn"))
            acc.add("tests", "test_name", f"test function {sym.get('name')}")

    if importers == 0 and internal_imports > 0 and not acc.scores.get("tests"):
        acc.add("api", "graph", "no project file imports it, and it imports project files")
    if importers >= 3 and internal_imports == 0:
        acc.add("utilities", "position", f"imported by {importers} files, imports none")
    return acc


def _is_entry(snap: Snapshot, f: dict, importers: Counter, file_imports: dict) -> bool:
    """Observed entry point: not imported by the project and it either defines `main`, a
    route/CLI handler, or has a conventional entry-point file name."""
    if importers[f["id"]]:
        return False
    stem = posixpath.splitext(posixpath.basename(f.get("path", "")))[0]
    if stem in ENTRY_NAMES:
        return True
    for sym in _symbols_of_file(snap, f["id"]):
        if sym.get("name") == "main" and sym.get("label") == "Function":
            return True
        if any(rx.search(d.split("(")[0].lstrip("@")) for d in sym.get("decorators") or []
               for rx, layer, _ in DECORATOR_HINTS if layer == "api"):
            return True
    return False


def _scc(graph: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one member (Tarjan, iterative)."""
    index, low, on_stack, stack, out, counter = {}, {}, set(), [], [], [0]
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph.get(nxt, ())))))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))
    return sorted(out, key=lambda c: (-len(c), c))


# ------------------------------------------------------------------ analysis

def analyze_architecture(snap: Snapshot) -> ArchitectureReport:
    files = snap.of_label("File")
    per_file = len(files) <= FILE_COMPONENT_LIMIT
    path_of = {f["id"]: f.get("path", f["id"]) for f in files}
    comp_of_file = {f["id"]: component_of(f.get("path", ""), per_file) for f in files}

    file_imports: dict[str, set[str]] = defaultdict(set)
    importers: Counter = Counter()
    for e in snap.edges:
        if e.type == "IMPORTS" and e.source in path_of and e.target in path_of \
                and e.source != e.target:
            file_imports[e.source].add(e.target)
            importers[e.target] += 1

    # Trivial package markers (empty __init__.py) carry no architectural information.
    trivial = {f["id"] for f in files if posixpath.basename(f.get("path", "")) == "__init__.py"
               and not int(f.get("symbol_count") or 0) and not file_imports[f["id"]]}
    files = [f for f in files if f["id"] not in trivial]

    roots = _root_packages([f.get("path", "") for f in files])
    accs = {f["id"]: _file_evidence(snap, f, importers[f["id"]], len(file_imports[f["id"]]),
                                    roots)
            for f in files}
    first = {fid: _decide(acc.scores)[0] for fid, acc in accs.items()}
    # Dependency position: used by the API/UI layer and itself using data/infrastructure/
    # domain code is the observable signature of business logic.
    users_of: dict[str, set[str]] = defaultdict(set)
    for src, targets in file_imports.items():
        for dst in targets:
            users_of[dst].add(src)
    for fid, acc in accs.items():
        if first[fid] not in (UNCLASSIFIED, "utilities") and acc.scores[first[fid]] >= 3:
            continue
        used_by = {first.get(u) for u in users_of[fid]} & {"api", "presentation"}
        uses = {first.get(t) for t in file_imports[fid]} & {"data", "infrastructure", "domain"}
        if used_by and uses:
            acc.add("business", "position",
                    f"used by the {'/'.join(sorted(used_by))} layer and uses the "
                    f"{'/'.join(sorted(uses))} layer")

    file_layers: list[FileLayer] = []
    comp_scores: dict[str, Counter] = defaultdict(Counter)
    comp_evidence: dict[str, Counter] = defaultdict(Counter)
    for f in files:
        acc = accs[f["id"]]
        layer, conf, _ = _decide(acc.scores)
        comp = comp_of_file[f["id"]]
        file_layers.append(FileLayer(file_id=f["id"], path=path_of[f["id"]], component=comp,
                                     layer=layer, confidence=conf, scores=dict(acc.scores),
                                     evidence=acc.evidence))
        comp_scores[comp].update(acc.scores)
        for ev in acc.evidence:
            comp_evidence[comp][f"{ev.detail} [{ev.layer}]"] += 1

    # Component dependencies (observed IMPORTS / CALLS between files of different components)
    comp_imports: Counter = Counter()
    comp_calls: Counter = Counter()
    example: dict[tuple[str, str], str] = {}
    for src, targets in file_imports.items():
        for dst in targets:
            if src in trivial or dst in trivial:
                continue
            a, b = comp_of_file[src], comp_of_file[dst]
            if a != b:
                comp_imports[(a, b)] += 1
                example.setdefault((a, b), f"{path_of[src]} imports {path_of[dst]}")
    for e in snap.edges:
        if e.type == "CALLS":
            fa, fb = snap.file_of(e.source), snap.file_of(e.target)
            if fa in comp_of_file and fb in comp_of_file and comp_of_file[fa] != comp_of_file[fb] \
                    and fa not in trivial and fb not in trivial:
                comp_calls[(comp_of_file[fa], comp_of_file[fb])] += 1

    components: list[Component] = []
    files_by_comp: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        files_by_comp[comp_of_file[f["id"]]].append(f)
    comp_layer: dict[str, str] = {}
    for comp in sorted(files_by_comp):
        members = files_by_comp[comp]
        # normalise by file count so one large component is not "more certain" by size alone
        scores = Counter({k: v / max(1, len(members)) ** 0.5 for k, v in comp_scores[comp].items()})
        layer, conf, ambiguous = _decide(scores)
        comp_layer[comp] = layer
        entry = sorted(path_of[m["id"]] for m in members
                       if layer != "tests" and _is_entry(snap, m, importers, file_imports))
        components.append(Component(
            name=comp, layer=layer, confidence=conf, ambiguous_between=ambiguous,
            scores={k: round(v, 2) for k, v in scores.most_common()},
            evidence=[f"{d} ×{n}" if n > 1 else d for d, n in comp_evidence[comp].most_common(6)],
            files=sorted(path_of[m["id"]] for m in members),
            symbol_count=sum(int(m.get("symbol_count") or 0) for m in members),
            loc=sum(int(m.get("loc") or 0) for m in members),
            depends_on=sorted((ComponentDep(component=b, imports=n, calls=comp_calls[(a, b)])
                               for (a, b), n in comp_imports.items() if a == comp),
                              key=lambda d: (-d.imports, d.component)),
            used_by=sorted((ComponentDep(component=a, imports=n, calls=comp_calls[(a, b)])
                            for (a, b), n in comp_imports.items() if b == comp),
                           key=lambda d: (-d.imports, d.component)),
            entry_points=entry))

    layer_imports: Counter = Counter()
    violations: list[Violation] = []
    confident = {c.name for c in components if c.confidence in ("medium", "high")}
    for (a, b), n in sorted(comp_imports.items()):
        la, lb = comp_layer[a], comp_layer[b]
        layer_imports[(la, lb)] += n
        # only reported when both classifications are reasonably supported
        if la in RANK and lb in RANK and RANK[la] > RANK[lb] and a in confident and b in confident:
            violations.append(Violation(source_component=a, target_component=b, source_layer=la,
                                        target_layer=lb, imports=n, example=example[(a, b)]))
    layer_edges = [LayerEdge(source=la, target=lb, imports=n,
                             possible_violation=la in RANK and lb in RANK and RANK[la] > RANK[lb])
                   for (la, lb), n in sorted(layer_imports.items()) if la != lb]

    order = {layer: i for i, layer in enumerate(LAYERS + [UNCLASSIFIED])}
    layers = []
    for layer in sorted({c.layer for c in components}, key=order.get):
        comps = [c for c in components if c.layer == layer]
        layers.append(LayerSummary(layer=layer, title=LAYER_TITLES[layer],
                                   components=[c.name for c in comps],
                                   file_count=sum(len(c.files) for c in comps)))

    if trivial:
        notes_extra = [f"{len(trivial)} empty package marker file(s) (__init__.py) omitted."]
    else:
        notes_extra = []
    comp_graph = defaultdict(set)
    for a, b in comp_imports:
        comp_graph[a].add(b)
    file_graph = {f: set(t) for f, t in file_imports.items() if f not in trivial}
    notes = [
        "Possible violations are reported only between components classified with at least "
        "medium confidence, against the conventional direction presentation → api → "
        "business → data/infrastructure → domain → utilities.",
        "Layers are inferred from evidence (framework imports, decorators, base classes, "
        "names and import structure); relationships and evidence items are observed.",
        "Only resolved project imports are used for dependencies; dynamic imports and "
        "runtime wiring are not visible.",
    ]
    notes += notes_extra
    if not snap.edges:
        notes.append("The project has no relationships; dependencies cannot be shown.")
    unclassified = [c.name for c in components if c.layer == UNCLASSIFIED]
    if unclassified:
        notes.append(f"{len(unclassified)} component(s) have too little or conflicting "
                     "evidence and are left unclassified rather than guessed.")
    entry_points = sorted({p for c in components for p in c.entry_points})
    if not entry_points:
        notes.append("No entry point was detected (no `main`, route/CLI handler or entry-style "
                     "file that nothing imports).")
    if per_file:
        notes.append(f"Small project ({len(files)} files): each file is shown as a component.")
    return ArchitectureReport(
        project_id=snap.project_id, layers=layers, components=components, files=file_layers,
        layer_edges=layer_edges, violations=violations, component_cycles=_scc(comp_graph),
        file_cycles=[[path_of[f] for f in c] for c in _scc(file_graph)][:20],
        entry_points=entry_points, notes=notes)


def layer_of_file(report: ArchitectureReport) -> dict[str, tuple[str, str]]:
    """file path -> (component, component layer)."""
    comp_layer = {c.name: c.layer for c in report.components}
    return {f.path: (f.component, comp_layer[f.component]) for f in report.files}
