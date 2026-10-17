"""Evidence-backed explanations of a single function, method, class, file or module.

`facts` are observed: graph properties and relationships of the symbol and its direct
neighbours, plus (for local projects whose files are unchanged since analysis) a verified
source excerpt. `unknowns` lists what cannot be established. `interpretation` is the
optional LLM text written only from those facts; it is None when the LLM is not requested
or not available, in which case the facts stand on their own.
"""

import hashlib
import os
from typing import Any

from pydantic import BaseModel

from codeatlas.intel.architecture import LAYER_TITLES, ArchitectureReport, layer_of_file
from codeatlas.intel.snapshot import Snapshot

MAX_REFS = 25
MAX_EXCERPT_LINES = 150
SIDE_EFFECT_LAYERS = {"data": "persistence", "infrastructure": "external integration"}


class SymbolNotFound(LookupError):
    pass


class Ref(BaseModel):
    node_id: str
    label: str
    display: str
    location: dict[str, Any] | None = None
    lines: list[int] = []
    confidence: str | None = None
    layer: str | None = None


class SourceExcerpt(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    text: str
    verified: bool  # file content hash equals the hash recorded at analysis time


class SymbolFacts(BaseModel):
    node_id: str
    label: str
    kind: str  # function | method | async function | class | file | external module
    name: str
    qualname: str | None = None
    location: dict[str, Any] | None = None
    layer: str | None = None
    component: str | None = None
    signature: str | None = None
    params: list[str] | None = None
    decorators: list[str] = []
    bases: list[str] = []
    docstring: str | None = None
    belongs_to: list[Ref] = []
    calls: list[Ref] = []
    callers: list[Ref] = []
    methods: list[Ref] = []
    parents: list[Ref] = []
    subclasses: list[Ref] = []
    defines: list[Ref] = []
    imports: list[Ref] = []
    imported_by: list[Ref] = []
    external_calls: list[str] = []
    unresolved_calls: list[str] = []
    side_effect_hints: list[str] = []
    counts: dict[str, int] = {}
    unknowns: list[str] = []
    source_excerpt: SourceExcerpt | None = None


class Explanation(BaseModel):
    facts: SymbolFacts
    interpretation: str | None = None
    llm_used: bool = False
    llm_error: dict[str, Any] | None = None


def _ref(snap: Snapshot, nid: str, edge=None, ctx=None) -> Ref:
    n = snap.nodes[nid]
    layer = None
    if ctx:
        path = n.get("path") or n.get("file_path")
        layer = ctx.get(path, (None, None))[1] if path else None
    return Ref(node_id=nid, label=n["label"], display=snap.display(nid),
               location=snap.location(nid),
               lines=list((edge.props.get("lines") or []) if edge else [])[:10],
               confidence=edge.props.get("confidence") if edge else None, layer=layer)


def _refs(snap, edges, end: str, ctx) -> tuple[list[Ref], int]:
    ids = []
    for e in sorted(edges, key=lambda e: getattr(e, end)):
        nid = getattr(e, end)
        if nid in snap.nodes:
            ids.append((nid, e))
    return [_ref(snap, nid, e, ctx) for nid, e in ids[:MAX_REFS]], len(ids)


def read_excerpt(source_root: str | None, file_props: dict | None, start: int | None,
                 end: int | None) -> SourceExcerpt | None:
    """Read lines from the analyzed file if it is still on disk, inside the project root and
    byte-identical to what was analyzed (sha256). Otherwise None (never a guess)."""
    if not source_root or not file_props or not file_props.get("path"):
        return None
    root = os.path.realpath(source_root)
    full = os.path.realpath(os.path.join(root, file_props["path"]))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    try:
        with open(full, "rb") as fh:
            data = fh.read(2_000_000)
    except OSError:
        return None
    if file_props.get("sha256") and hashlib.sha256(data).hexdigest() != file_props["sha256"]:
        return None  # changed since analysis: line numbers may no longer match
    lines = data.decode("utf-8", errors="replace").splitlines()
    s = max(1, int(start or 1))
    e = min(len(lines), int(end or len(lines)), s + MAX_EXCERPT_LINES - 1)
    if s > len(lines):
        return None
    return SourceExcerpt(file_path=file_props["path"], start_line=s, end_line=e,
                         text="\n".join(lines[s - 1:e]), verified=True)


def symbol_facts(snap: Snapshot, report: ArchitectureReport | None, node_id: str,
                 source_root: str | None = None) -> SymbolFacts:
    n = snap.nodes.get(node_id)
    if n is None:
        raise SymbolNotFound(f"No symbol {node_id!r} in project {snap.project_id}")
    ctx = layer_of_file(report) if report else {}
    label = n["label"]
    path = n.get("path") or n.get("file_path")
    comp, layer = ctx.get(path, (None, None)) if path else (None, None)
    f = SymbolFacts(node_id=node_id, label=label, kind=label.lower(),
                    name=n.get("name") or n.get("path") or node_id, qualname=n.get("qualname"),
                    location=snap.location(node_id), layer=layer, component=comp,
                    decorators=list(n.get("decorators") or []), bases=list(n.get("bases") or []),
                    docstring=n.get("docstring"))
    unknowns: list[str] = []

    if label == "Function":
        f.kind = ("async " if n.get("is_async") else "") + ("method" if n.get("is_method")
                                                            else "function")
        f.params = list(n.get("params") or [])
        f.signature = f"{n.get('name')}({', '.join(f.params)})"
        f.calls, f.counts["calls"] = _refs(snap, snap.out_edges(node_id, "CALLS"), "target", ctx)
        f.callers, f.counts["callers"] = _refs(snap, snap.in_edges(node_id, "CALLS"), "source",
                                               ctx)
        f.belongs_to = [_ref(snap, e.source, ctx=ctx) for e in snap.in_edges(node_id)
                        if e.type in ("DEFINES", "HAS_METHOD") and e.source in snap.nodes]
        f.external_calls = list(n.get("external_calls") or [])
        f.unresolved_calls = list(n.get("unresolved_calls") or [])
        unknowns += ["Parameter types and the return value are not extracted by the analyzer.",
                     "Callers through callbacks, dynamic dispatch or frameworks are not visible."]
        if f.unresolved_calls:
            unknowns.append(f"{len(f.unresolved_calls)} call(s) could not be resolved to a "
                            "project symbol.")
    elif label == "Class":
        f.kind = "class"
        f.methods, f.counts["methods"] = _refs(snap, snap.out_edges(node_id, "HAS_METHOD"),
                                               "target", ctx)
        f.parents, _ = _refs(snap, snap.out_edges(node_id, "INHERITS"), "target", ctx)
        f.subclasses, f.counts["subclasses"] = _refs(snap, snap.in_edges(node_id, "INHERITS"),
                                                     "source", ctx)
        f.belongs_to = [_ref(snap, e.source, ctx=ctx) for e in snap.in_edges(node_id, "DEFINES")
                        if e.source in snap.nodes]
        callers = [e for m in snap.out_edges(node_id, "HAS_METHOD")
                   for e in snap.in_edges(m.target, "CALLS")]
        f.callers, f.counts["callers_of_methods"] = _refs(snap, callers, "source", ctx)
        resolved_parents = {r.display.rsplit("::", 1)[-1] for r in f.parents}
        external = [b for b in f.bases if b.rsplit(".", 1)[-1] not in resolved_parents]
        if external:
            unknowns.append("Base classes outside the project (not analyzed): "
                            + ", ".join(external))
        unknowns.append("Attributes and instantiation sites are not extracted.")
    elif label == "File":
        f.kind = "file"
        f.qualname = n.get("module")
        f.defines, f.counts["defines"] = _refs(snap, snap.out_edges(node_id, "DEFINES"),
                                               "target", ctx)
        f.imports, f.counts["imports"] = _refs(snap, snap.out_edges(node_id, "IMPORTS"),
                                               "target", ctx)
        f.imported_by, f.counts["imported_by"] = _refs(snap, snap.in_edges(node_id, "IMPORTS"),
                                                       "source", ctx)
        f.location = {"file_path": n.get("path"), "start_line": 1, "end_line": n.get("loc")}
        if n.get("has_syntax_errors"):
            unknowns.append("The file had syntax errors; extraction may be partial.")
    elif label == "Module":
        f.kind = "external module"
        f.imported_by, f.counts["imported_by"] = _refs(snap, snap.in_edges(node_id, "IMPORTS"),
                                                       "source", ctx)
        unknowns.append("External module: its source is not part of the analyzed project.")

    # potential side effects, only from observed calls / imports
    for r in f.calls:
        if r.layer in SIDE_EFFECT_LAYERS:
            f.side_effect_hints.append(f"calls {r.display} in the {LAYER_TITLES[r.layer]} layer "
                                       f"({SIDE_EFFECT_LAYERS[r.layer]})")
    if f.external_calls:
        f.side_effect_hints.append("calls external code: " + ", ".join(f.external_calls[:8]))

    if label in ("Function", "Class", "File"):
        file_id = snap.file_of(node_id)
        file_props = snap.nodes.get(file_id) if file_id else None
        start = n.get("start_line") if label != "File" else 1
        end = n.get("end_line") if label != "File" else min(int(n.get("loc") or 60), 60)
        f.source_excerpt = read_excerpt(source_root, file_props, start, end)
        if f.source_excerpt is None:
            unknowns.append("Source text is not stored in the graph and the analyzed file is not "
                            "available unchanged on this machine, so the body was not read; "
                            "facts come from the signature, docstring and relationships.")
    if not f.location:
        unknowns.append("No source location is recorded for this node.")
    elif label in ("Function", "Class") and not n.get("start_line"):
        unknowns.append("Line range missing in the stored graph (re-analysis would add it).")
    if not f.docstring and label != "Module":
        unknowns.append("No docstring.")
    f.unknowns = unknowns
    return f


EXPLAIN_SYSTEM = (
    "You explain one code symbol to a developer using ONLY the facts and the source excerpt "
    "provided. Write concise markdown with these sections: **What it does**, "
    "**Responsibilities**, **Notable behaviour** (including side effects only if the facts "
    "or source show them), **Uncertainties**. Never invent names, callers, line numbers, "
    "parameter types or return values; if something is not in the facts, say it is unknown.")


def facts_prompt(f: SymbolFacts, max_chars: int = 9000) -> str:
    def refs(title, items, count_key=None):
        if not items:
            return []
        extra = f" (showing {len(items)} of {f.counts.get(count_key)})" \
            if count_key and f.counts.get(count_key, 0) > len(items) else ""
        return [f"{title}{extra}:"] + [
            f"  - {r.display}" + (f" lines {r.lines}" if r.lines else "")
            + (f" [{r.confidence}]" if r.confidence else "") for r in items]

    loc = f.location or {}
    lines = [f"Symbol: {f.kind} {f.qualname or f.name}",
             f"Location: {loc.get('file_path')} lines {loc.get('start_line')}-{loc.get('end_line')}",
             f"Layer (inferred): {f.layer or 'unknown'}; component: {f.component or 'unknown'}"]
    if f.signature:
        lines.append(f"Signature (names only): {f.signature}")
    for title, value in (("Decorators", f.decorators), ("Bases", f.bases),
                         ("External calls", f.external_calls),
                         ("Unresolved calls", f.unresolved_calls),
                         ("Possible side effects", f.side_effect_hints)):
        if value:
            lines.append(f"{title}: {', '.join(value)}")
    if f.docstring:
        lines.append(f"Docstring: {f.docstring[:800]}")
    for title, items, key in (("Belongs to", f.belongs_to, None), ("Calls", f.calls, "calls"),
                              ("Called by", f.callers, "callers"),
                              ("Methods", f.methods, "methods"), ("Parents", f.parents, None),
                              ("Subclasses", f.subclasses, "subclasses"),
                              ("Defines", f.defines, "defines"), ("Imports", f.imports, "imports"),
                              ("Imported by", f.imported_by, "imported_by")):
        lines += refs(title, items, key)
    lines.append("Unknowns: " + " ".join(f.unknowns))
    if f.source_excerpt:
        ex = f.source_excerpt
        lines.append(f"Source excerpt ({ex.file_path} lines {ex.start_line}-{ex.end_line}):")
        lines.append(ex.text)
    return "\n".join(lines)[:max_chars]
