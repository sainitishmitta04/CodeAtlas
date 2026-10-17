"""Intent search over one project: lexical + graph ranking (no embeddings).

Mode name: `lexical+graph`. This is *not* semantic/vector search. A natural-language query
is reduced to content terms (stop words removed, identifiers split, light stemming), each
term is matched against a symbol's name, qualified name, path, docstring, parameters,
decorators and the names it calls or imports; a small, explicit synonym table expands
common programming intents (e.g. "authentication" → login, token, password). Scores are
weighted by how rare a term is in the project (idf) and by graph signals (how many callers
or importers a symbol has). Every result lists the signals that produced its score.

Genuine semantic retrieval would need an embedding model (an external API call per
symbol, or a new local dependency) plus stored vectors or a vector index (an Aura write);
none of that is enabled here.
"""

import difflib
import math
import re
from collections import Counter
from typing import Any

from pydantic import BaseModel

from codeatlas.intel.architecture import ArchitectureReport, layer_of_file
from codeatlas.intel.snapshot import Snapshot

SEARCH_MODE = "lexical+graph"
SEARCH_NOTE = ("Matches identifiers, paths, docstrings, parameters, decorators and called / "
               "imported names, with synonym expansion and graph-based ranking. Semantic "
               "(embedding) search is not enabled.")
STOP = set("""a an and are as at be by can code do does find for from get handle handled
handles how i in into is it its me my of on or show some that the their there this to under
use used uses using what when where which who why with function functions method methods
class classes file files module modules thing things part parts place logic does done""".split())
SYNONYMS = {
    "auth": ["login", "password", "token", "credential", "session", "authenticate", "oauth",
             "jwt", "permission"],
    "authent": ["auth", "login", "password", "token", "credential", "session"],
    "login": ["auth", "password", "session", "credential"],
    "valid": ["check", "verify", "validate", "sanitize", "ensure", "assert", "reject", "safe"],
    "check": ["valid", "verify"],
    "verify": ["valid", "check"],
    "retry": ["attempt", "backoff", "retries", "again", "transient"],
    "fail": ["error", "exception", "raise", "failure"],
    "error": ["exception", "fail", "raise"],
    "databas": ["db", "neo4j", "sql", "session", "driver", "query", "cypher", "connection"],
    "db": ["databas", "driver", "session", "query"],
    "connect": ["connection", "driver", "client", "session"],
    "connection": ["connect", "driver", "session", "client"],
    "initial": ["init", "setup", "create", "driver", "start"],
    "init": ["initial", "setup", "create"],
    "upload": ["zip", "archive", "file", "extract"],
    "archive": ["zip", "extract", "upload"],
    "download": ["fetch", "http", "request", "archive"],
    "request": ["http", "client", "fetch", "call"],
    "http": ["request", "client", "httpx", "url"],
    "api": ["endpoint", "route", "http", "request"],
    "endpoint": ["api", "route"],
    "config": ["setting", "env", "environ"],
    "setting": ["config", "env"],
    "pars": ["extract", "tree", "ast", "token"],
    "extract": ["pars", "scan"],
    "transform": ["convert", "build", "map", "pars", "row", "payload"],
    "convert": ["transform", "build", "map"],
    "raw": ["input", "pars"],
    "domain": ["model", "entity", "schema"],
    "object": ["model", "row", "node"],
    "search": ["find", "lookup", "match", "query"],
    "graph": ["node", "relationship", "edge", "cypher", "neo4j"],
    "sync": ["write", "merge", "sweep", "ingest"],
    "write": ["sync", "merge", "save", "store"],
    "save": ["write", "store", "persist"],
    "delet": ["remov", "sweep", "drop"],
    "test": ["assert", "fixture", "mock"],
    "llm": ["model", "chat", "prompt", "gemini", "openai"],
    "prompt": ["llm", "chat"],
    "secret": ["key", "token", "credential", "redact"],
    "log": ["logger", "logging", "warn"],
    "cache": ["memo", "lru"],
    "rate": ["limit", "throttl"],
}
FIELD_WEIGHTS = {"name": 4.0, "qualname": 2.5, "path": 2.0, "doc": 1.5, "params": 1.0,
                 "decorators": 1.5, "calls": 1.0}
SYNONYM_FACTOR = 0.35
FUZZY_FACTOR = 0.6
LABEL_FACTOR = {"Function": 1.0, "Class": 1.0, "File": 0.9, "Module": 0.5}


class SearchResult(BaseModel):
    node_id: str
    label: str
    name: str
    display: str
    location: dict[str, Any] | None = None
    layer: str | None = None
    component: str | None = None
    score: float
    matched: list[str]  # human-readable reasons
    summary: str | None = None


class SearchResponse(BaseModel):
    query: str
    terms: list[str]
    expanded_terms: dict[str, list[str]]
    mode: str = SEARCH_MODE
    semantic: bool = False
    note: str = SEARCH_NOTE
    total: int
    offset: int
    limit: int
    results: list[SearchResult]


def _split_identifiers(text: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return [t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t]


def stem(word: str) -> str:
    if word.endswith(("ies", "ied")) and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ations", "ation", "ings", "ing", "ions", "ion", "ers", "er", "ed", "es",
                   "s", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def terms_of(text: str) -> list[str]:
    return [stem(t) for t in _split_identifiers(text)]


def query_terms(query: str) -> list[str]:
    out = []
    for t in _split_identifiers(query):
        if t in STOP or len(t) < 2:
            continue
        s = stem(t)
        if s not in out:
            out.append(s)
    return out


class _Doc:
    __slots__ = ("nid", "fields", "all_terms", "name_terms", "graph")

    def __init__(self, nid: str, fields: dict[str, set[str]], graph: int):
        self.nid, self.fields, self.graph = nid, fields, graph
        self.all_terms = set().union(*fields.values()) if fields else set()
        self.name_terms = fields.get("name", set())


class SearchIndex:
    def __init__(self, snap: Snapshot):
        self.snap = snap
        self.docs: list[_Doc] = []
        df: Counter = Counter()
        for n in snap.nodes.values():
            nid = n["id"]
            calls = [snap.nodes[e.target].get("name", "") for e in snap.out_edges(nid, "CALLS")
                     if e.target in snap.nodes]
            calls += list(n.get("external_calls") or []) + list(n.get("unresolved_calls") or [])
            imports = [snap.nodes[e.target].get("name") or snap.nodes[e.target].get("path", "")
                       for e in snap.out_edges(nid, "IMPORTS") if e.target in snap.nodes]
            fields = {
                "name": set(terms_of(n.get("name") or "")),
                "qualname": set(terms_of(n.get("qualname") or n.get("module") or "")),
                "path": set(terms_of(n.get("path") or n.get("file_path") or "")),
                "doc": set(terms_of(n.get("docstring") or "")),
                "params": set(terms_of(" ".join(n.get("params") or []))),
                "decorators": set(terms_of(" ".join(n.get("decorators") or []))),
                "calls": set(terms_of(" ".join(calls + imports))),
            }
            fields = {k: v for k, v in fields.items() if v}
            graph = len(snap.in_edges(nid, "CALLS")) + len(snap.in_edges(nid, "IMPORTS"))
            doc = _Doc(nid, fields, graph)
            self.docs.append(doc)
            df.update(doc.all_terms)
        self.df, self.n_docs = df, max(1, len(self.docs))
        self.vocab = sorted(df)

    def idf(self, term: str) -> float:
        return 1.0 + math.log(self.n_docs / (1 + self.df.get(term, 0)))

    def expand(self, terms: list[str]) -> dict[str, list[str]]:
        out = {}
        for t in terms:
            syn = []
            for key, values in SYNONYMS.items():
                if t == key or (len(key) >= 4 and t.startswith(key)) \
                        or (len(t) >= 4 and key.startswith(t)):
                    syn += [stem(v) for v in values] + ([key] if key != t else [])
            out[t] = sorted({s for s in syn if s != t and s in self.df})[:8]
        return out

    def fuzzy(self, term: str) -> list[str]:
        if len(term) < 4:
            return []
        return [w for w in difflib.get_close_matches(term, self.vocab, n=3, cutoff=0.84)
                if w != term]


def get_index(snap: Snapshot) -> SearchIndex:
    idx = getattr(snap, "_search_index", None)
    if idx is None:
        idx = SearchIndex(snap)
        snap._search_index = idx  # cached for the lifetime of this snapshot
    return idx


def search(snap: Snapshot, query: str, report: ArchitectureReport | None = None,
           labels: list[str] | None = None, layers: list[str] | None = None,
           limit: int = 20, offset: int = 0) -> SearchResponse:
    idx = get_index(snap)
    terms = query_terms(query)
    # a query term is never used as another term's synonym (no double counting)
    expanded = {t: [s for s in v if s not in terms] for t, v in idx.expand(terms).items()}
    fuzzy = {t: idx.fuzzy(t) for t in terms if t not in idx.df}
    ctx = layer_of_file(report) if report else {}
    raw_query = re.sub(r"\s+", "", query.strip().lower())
    scored: list[tuple[float, str, list[str]]] = []
    by_reason_cap = 6
    for doc in idx.docs:
        n = idx.snap.nodes[doc.nid]
        if labels and n["label"] not in labels:
            continue
        path = n.get("path") or n.get("file_path")
        comp, layer = ctx.get(path, (None, None)) if path else (None, None)
        if layers and (layer or "unclassified") not in layers:
            continue
        score, reasons, matched_terms, used = 0.0, [], set(), set()
        for t in terms:
            best, why, best_variant = 0.0, None, None
            for variant, factor, kind in ([(t, 1.0, "")]
                                          + [(s, SYNONYM_FACTOR, f"synonym of '{t}'")
                                             for s in expanded.get(t, [])]
                                          + [(s, FUZZY_FACTOR, f"close to '{t}'")
                                             for s in fuzzy.get(t, [])]):
                if variant not in doc.all_terms:
                    continue
                if variant != t and variant in used:
                    factor *= 0.5  # a synonym shared by several query terms counts less again
                for field, weight in FIELD_WEIGHTS.items():
                    if variant in doc.fields.get(field, ()):
                        value = weight * factor * idx.idf(variant)
                        if value > best:
                            best, best_variant = value, variant
                            why = f"{field} contains '{variant}'" + (f" ({kind})" if kind else "")
            if best:
                score += best
                used.add(best_variant)
                matched_terms.add(t)
                reasons.append(why)
        name_compact = (n.get("name") or "").lower()
        if raw_query and raw_query in (name_compact, (n.get("qualname") or "").lower(),
                                       (n.get("path") or "").lower()):
            score += 12
            reasons.insert(0, "exact name match")
        elif raw_query and len(raw_query) >= 4 and raw_query in (n.get("qualname") or n.get("path")
                                                                 or name_compact).lower():
            score += 5
            reasons.insert(0, "name contains the query")
        if score <= 0:
            continue
        if terms:
            coverage = len(matched_terms) / len(terms)
            score *= 0.3 + coverage ** 1.5  # results covering every term rank first
            if len(terms) > 1:
                reasons.append(f"matches {len(matched_terms)}/{len(terms)} query terms")
        if doc.graph:
            score += 0.4 * math.log1p(doc.graph)
            reasons.append(f"{doc.graph} incoming call/import relationship(s)")
        score *= LABEL_FACTOR.get(n["label"], 1.0)
        scored.append((round(score, 3), doc.nid, reasons[:by_reason_cap]))
    scored.sort(key=lambda r: (-r[0], r[1]))
    results = []
    for score, nid, reasons in scored[offset:offset + limit]:
        n = snap.nodes[nid]
        path = n.get("path") or n.get("file_path")
        comp, layer = ctx.get(path, (None, None)) if path else (None, None)
        doc = (n.get("docstring") or "").strip().splitlines()
        results.append(SearchResult(
            node_id=nid, label=n["label"], name=n.get("qualname") or n.get("name") or path or nid,
            display=snap.display(nid), location=snap.location(nid), layer=layer, component=comp,
            score=score, matched=reasons, summary=doc[0][:160] if doc else None))
    return SearchResponse(query=query, terms=terms,
                          expanded_terms={**{t: v for t, v in expanded.items() if v},
                                          **{t: [f"~{w}" for w in v] for t, v in fuzzy.items()
                                             if v}},
                          total=len(scored), offset=offset, limit=limit, results=results)


__all__ = ["SEARCH_MODE", "SearchResponse", "SearchResult", "search", "query_terms", "stem"]
