"""Phase 2 entry points shared by the API (and usable from scripts).

Loads the project snapshot (cached per sync run), derives the architecture once per
snapshot, and calls the configured LLM only when explicitly requested. LLM failures never
fail the request: the deterministic result is returned with `llm_error` set.
"""

from codeatlas.graph import queries
from codeatlas.intel import explain as explain_mod
from codeatlas.intel import search as search_mod
from codeatlas.intel import tours as tours_mod
from codeatlas.intel.architecture import ArchitectureReport, analyze_architecture
from codeatlas.intel.snapshot import Snapshot, load_snapshot
from codeatlas.llm import client as llm


def context(pid: str) -> tuple[Snapshot, ArchitectureReport]:
    snap = load_snapshot(pid)
    report = getattr(snap, "_architecture", None)
    if report is None:
        report = analyze_architecture(snap)
        snap._architecture = report
    return snap, report


def architecture(pid: str) -> ArchitectureReport:
    return context(pid)[1]


def tour(pid: str, kind: str, target: str | None = None,
         narrate: bool = False) -> tours_mod.Tour:
    snap, report = context(pid)
    t = tours_mod.build_tour(snap, report, kind, target)
    if narrate and t.steps:
        try:
            t.narrative = llm.chat(tours_mod.NARRATE_SYSTEM, tours_mod.tour_prompt(t),
                                   max_tokens=900)
        except llm.LLMError as exc:
            t.llm_error = exc.to_dict()
    return t


def _source_root(pid: str) -> str | None:
    """Local projects only: the analyzed directory, if the project was synced from one."""
    proj = queries.project(pid) or {}
    if proj.get("source_kind") not in (None, "local"):
        return None  # GitHub / ZIP sources were temporary checkouts; nothing on disk
    return proj.get("root_path")


def explain(pid: str, node_id: str, use_llm: bool = False) -> explain_mod.Explanation:
    snap, report = context(pid)
    facts = explain_mod.symbol_facts(snap, report, node_id, _source_root(pid))
    out = explain_mod.Explanation(facts=facts)
    if use_llm:
        try:
            out.interpretation = llm.chat(explain_mod.EXPLAIN_SYSTEM,
                                          explain_mod.facts_prompt(facts), max_tokens=800)
            out.llm_used = True
        except llm.LLMError as exc:
            out.llm_error = exc.to_dict()
    return out


def search(pid: str, q: str, labels: list[str] | None = None, layers: list[str] | None = None,
           limit: int = 20, offset: int = 0) -> search_mod.SearchResponse:
    snap, report = context(pid)
    return search_mod.search(snap, q, report, labels, layers, max(1, min(limit, 100)),
                             max(0, offset))
