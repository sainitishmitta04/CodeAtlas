# Phase 2: Architecture Intelligence

CodeAtlas gains four connected capabilities, each computed from the selected project's graph in Neo4j:

- **Architecture**: inferred layers and components, the dependencies between them, possible layering violations and cycles.
- **Tours**: guided walkthroughs whose steps come from deterministic graph traversal.
- **Explain**: evidence-backed explanations of a function, method, class, file or module.
- **Search**: finding code by describing it, using lexical and graph signals (not embeddings).

Nothing is hardcoded or canned. Neo4j's schema and data are unchanged: no migration, no new properties, no Aura writes, and no re-ingestion. The LLM (your configured Gemini model) is optional. It is only called when you click a *Narrate* or *Generate LLM interpretation* control, and every feature works without it.

---

## 1. How it works

```
Neo4j (existing File/Class/Function/Module nodes + DEFINES/HAS_METHOD/IMPORTS/INHERITS/CALLS)
   │  2 bounded, project-scoped read queries (SNAPSHOT_NODES, snapshot_rels_query)
   ▼
Snapshot (in memory, cached per project sync run)  ──►  architecture.py  (layers)
                                                     ├─► tours.py         (tours)
                                                     ├─► explain.py       (facts → optional LLM)
                                                     └─► search.py        (lexical + graph)
   ▼
FastAPI  /projects/{pid}/architecture | /tours | /explain | /search   ──►  Streamlit tabs
```

- **One snapshot per project.** All four features work on the same in-memory view: the project's nodes and relationships, read with two label-anchored queries filtered by `project_id`. That avoids one query per node, never mixes projects, and drops any relationship whose endpoint lies outside the project.
  - The snapshot is cached until the project's `last_run_id` changes, so a re-sync refreshes it.
  - The limit is 30,000 nodes (projects are already capped at 25,000 when synced).
- **The code lives in `src/codeatlas/intel/`:**
  - `snapshot.py`: loading and caching the snapshot;
  - `architecture.py`, `tours.py`, `explain.py`, `search.py`: one module per feature;
  - `service.py`: the shared entry points used by the API.
- **Offline equivalence:** `Snapshot.from_rows()` builds the same structure straight from analysis output. The tests use it to run every feature on real analyzed repositories without Neo4j.

---

## 2. Architecture (tab **Architecture**, `GET /projects/{pid}/architecture`)

### Components

- In small projects (40 files or fewer), every file is a component.
- In larger projects, a component is a source directory, up to two levels below the root. A leading `src/` or `lib/` is ignored, and files at the root are grouped as `(root)`.
- Empty `__init__.py` files are left out.

### Layers

The possible layers are presentation, api (including entry points), business, domain, data, infrastructure, utilities and tests.

Each file collects weighted evidence. The ordering is deliberate: what the code *does* outweighs what it is *called*.

| Evidence | Weight | Examples |
|---|---|---|
| Framework or library imports | strong (3) | `fastapi`, `flask`, `click` → api; `streamlit` → presentation; `sqlalchemy`, `neo4j`, `sqlite3` → data; `httpx`, `requests`, `boto3` → infrastructure; `pytest` → tests |
| Decorators | strong (3) | `@router.post(...)` / `@app.get` → api; `@click.command` → api; `@pytest.fixture` → tests |
| Test naming | strong (3) | `test_*.py`, `test_*` functions |
| Base classes | medium (2) | `BaseModel` → domain; ORM `Model`/`Base` → data; `TestCase` → tests |
| Dependency position | medium (2) | used by the api/UI layer *and* uses data/infrastructure/domain code → business; a leaf imported by three or more files → utilities |
| Path and name tokens | weak (1.5) | `services/`, `models`, `db`, `utils`, … The project's own top-level package name (e.g. `app/`) is ignored. |
| Incidental signals | very weak (0.5) | `dataclasses`, `pydantic` imports, `@dataclass`, exception subclasses |

- **Decision:** the best-supported layer wins when its score is at least 1.5 and leads the runner-up by at least 1. Otherwise the component is left **unclassified**, and the competing candidates are shown under "ambiguous between".
- **Confidence:** *high*, *medium*, *low* or *none*.
- **Observed vs inferred:** each component lists its evidence items, which are observed. The layer itself is always an inference.

### Dependencies, violations and cycles

- **Dependencies:** resolved `IMPORTS` between files of different components. The number of `CALLS` between them is also given.
- **Possible violations:** a dependency running against the conventional direction (presentation → api → business → data/infrastructure → domain → utilities).
  - Reported only when both components are classified with at least *medium* confidence, so weak guesses never produce accusations.
  - Each one includes an example import.
  - Tests and unclassified components are never reported.
- **Cycles:** strongly connected components (Tarjan's algorithm), computed for components and for files.
- **Entry points:** files that no project file imports *and* that define `main`, a route or CLI handler, or have an entry-style name (`main`, `app`, `cli`, `server`, `manage`, …).

### UI

- One metric per layer.
- A layer diagram (Graphviz) with components grouped by layer and edges labelled with import counts. Violations are drawn in red. The diagram shows the 60 largest components; all are listed in the table below it.
- Notes, possible violations and cycles.
- **Inspect a component:** layer, confidence, evidence, files, "depends on" and "used by", entry points. Buttons start a **Component**, **Dependency** or **Impact** tour.
- **Per-file classification and evidence**, in a table.

---

## 3. Tours (tab **Tours**, `GET /projects/{pid}/tours?kind=…&target=…&narrate=false`)

| Kind | Target | What it follows |
|---|---|---|
| `overview` | — | The inferred layers in dependency order, then entry points, cycles and possible violations |
| `entry` | optional file path or ID | The detected entry file, then its `main` / route / top-level functions, then resolved `CALLS` breadth-first to depth 4 (exact resolutions first). If no calls are resolved, it falls back to the file's imports, with those steps marked **inferred** |
| `component` | component name | The component (layer and evidence), its files (most-imported first), its most-called symbols, and its dependencies |
| `execution` | function ID | Resolved `CALLS` depth-first to depth 5. Recursion and cycles are shown as `↺` and not expanded again. External and unresolved calls are listed but never followed |
| `dependency` | component name | Each component it depends on, with the observed import lines and imported names, plus external modules |
| `impact` | component name or node ID | Reverse `CALLS` / `IMPORTS` / `INHERITS`, up to three steps away, grouped by distance; ends with the affected components |

- **Each step** has a title, an explanation, its layer and component, a location (file and line range), evidence (the relationship observed) and an **observed** / *inferred* flag.
- **Size:** tours stop at 25 steps, and a truncated tour says so. The ordering is deterministic, so the same graph always gives the same tour.
- **Navigation:** ◀ Previous / Next ▶, a progress bar, an "All steps" list, **Explain** for symbol steps (shown inline), **Tour this component**, and **Back to the architecture overview**.
- **Narrate (LLM):** sends the finished steps (at most about 7,000 characters) and asks the model to describe them, not to add to them. If the LLM fails, the tour is still returned, with `llm_error` set and a warning shown.

---

## 4. Symbol explanations (tab **Explain**, `GET /projects/{pid}/explain?node_id=…&use_llm=false`)

| Section | Source |
|---|---|
| **Observed facts** | Graph properties and the direct neighbours of the symbol:<br>• kind, location and signature (parameter *names*), decorators, bases, docstring;<br>• what it belongs to, what it calls (with lines and resolution confidence) and who calls it;<br>• methods, parents and subclasses;<br>• defines, imports and imported-by;<br>• external and unresolved calls;<br>• possible side effects, *only* from observed calls into data/infrastructure code or external calls.<br>Lists are capped at 25 items, showing the total. |
| **Source excerpt (verified)** | Local projects only: the analyzed file is read from disk if it is still inside the project root **and its SHA-256 matches** the hash recorded at analysis time. Otherwise there is no excerpt, because the line numbers could be stale. GitHub and ZIP projects were temporary checkouts, so they never have one. Source text itself is not stored in the graph. |
| **Unknowns** | What cannot be established: parameter types and return values (not extracted), dynamic callers, unresolved calls, base classes outside the project, a missing docstring or line range, and whether the source body was read |
| **Interpretation** | Only when you click **Generate LLM interpretation** (`use_llm=true`). The model receives just this symbol's facts, its neighbours and the verified excerpt (at most about 9,000 characters), never the whole repository. It is told to use only those facts. If it fails, the facts are still shown and the error appears as a warning. |

You can open an explanation from Explain → *Find a symbol by name*, from **Use selected graph node** (the Graph tab), from a tour step, from a search result, or from any "Explain" next to a caller or callee. That last option lets you walk the call graph one symbol at a time.

---

## 5. Search (tab **Search**, `GET /projects/{pid}/search?q=…&labels=…&layers=…&limit=…&offset=…`)

**Mode: `lexical+graph`.** This is **not semantic or embedding search**, and the response says so (`semantic: false`).

- **Query terms:** stop words are removed, `snake_case` and `camelCase` are split, and words are lightly stemmed.
- **Matching:** each term is matched against a symbol's
  - name (weight 4), qualified name (2.5), path (2);
  - docstring (1.5), decorators (1.5);
  - parameters and the names it calls or imports (1).
- **Weighting:** rare terms count more (idf).
- **Expansion:**
  - an explicit synonym table (for example *authentication* → login, token, password; *database* → db, driver, session, cypher), at 0.35× weight;
  - fuzzy matches for typos (`difflib`, cutoff 0.84), at 0.6×.
  - A query term is never counted as another term's synonym.
- **Ranking:** results that match more of the query terms rank higher. Exact-name and contains-the-query bonuses apply, plus a small bonus for incoming call and import relationships. Modules are down-weighted.
- **Explanations:** every result lists *why* it matched, for example `name contains 'validat'` or `doc contains 'driv' (synonym of 'databas')`.
- **Filters and scope:** by symbol type and by inferred layer, paginated with *Show more results*, and always limited to the selected project.

**Semantic search is not implemented.** Real semantic retrieval would need:
1. an embedding model: either an external API call per symbol, which would send code text to the provider, or a new local dependency such as `sentence-transformers`, which needs your approval to install;
2. storing vectors on nodes plus a vector index, which is an Aura write and a schema change (AuraDB Free supports vector indexes);
3. re-embedding on every sync.

Each of these needs your approval. Lexical search already handles identifier-heavy queries well. It is weaker for purely conceptual wording that shares no words with the code or its docstrings.

---

## 6. API summary

| Endpoint | Success | Errors |
|---|---|---|
| `GET /projects/{pid}/architecture` | `ArchitectureReport` | 404 unknown project, 413 too large, 503 Neo4j |
| `GET /projects/{pid}/tours` | `Tour` (`narrative` / `llm_error` when `narrate=true`) | 400 bad kind or target, 404 |
| `GET /projects/{pid}/explain` | `Explanation` (`interpretation` / `llm_error` when `use_llm=true`) | 404 unknown project or node |
| `GET /projects/{pid}/search` | `SearchResponse` (`mode`, `semantic: false`, `note`, `results[].matched`) | 422 missing `q`, 404 |

LLM failures in these endpoints **do not** produce an error status, because the deterministic result is the answer. The failure is reported in `llm_error` instead. (`/ask` keeps its own behaviour: an LLM failure is an HTTP error there, because the LLM's text *is* the answer.)

---

## 7. Limitations

- **Python only.** The analysis is static: no runtime traces, dynamic imports, dependency injection or reflection. Execution paths follow statically resolved calls only, and `name_match` resolutions are labelled as such.
- **Layers are heuristic.** In unconventional repositories more components stay unclassified. That is intended: they are not guessed.
- **Not extracted:** parameter types, return values, attributes and instantiation sites.
- **Source excerpts** require a local project whose files are unchanged since its last sync. GitHub and ZIP projects show no excerpt.
- **Large projects:** the layer diagram shows the 60 largest components; tours stop at 25 steps (and say so); explanation lists stop at 25 items (with totals).

---

## 8. Running and testing

**Tests:** from `CodeAtlas/`, run `source ../.venv/bin/activate && python -m pytest`. This is offline: no Neo4j, GitHub or LLM calls.
- `tests/test_intel.py` (24): the layered fixture app (api → services → models/db, with a cycle and a violation, an unclassified module and tests), the sample fixture, and synthetic graphs. It covers:
  - layers, evidence, ambiguity, cycles, violations, entry points, directory components;
  - missing metadata, determinism;
  - every tour kind, ordering, missing entry points, incomplete call graphs, cycles and truncation, project-only content, errors;
  - function, method, class, file and module facts; verified and refused source excerpts; prompt scope;
  - search: exact and fuzzy matches, natural language, filters, pagination, empty results, honest mode, project isolation.
- `tests/test_intel_api.py` (16): every endpoint and error status; narration and interpretation success and graceful failure; remote projects get no excerpt; search scoping; snapshot loader scoping, caching and dropping of foreign edges; and the Streamlit flow (architecture → component tour → next step → explain → search → open result → explanation, plus the LLM-failure warning).

**Run the app:** see [RUN_AND_VERIFY.md](RUN_AND_VERIFY.md). The API must be restarted to load the new endpoints.

---

## 9. Manual verification (each step needs your approval)

1. **Restart** uvicorn and Streamlit, as in RUN_AND_VERIFY §3. The four new tabs should appear: *Architecture*, *Tours*, *Explain*, *Search*.
2. **Charlie and sample:** select `charlie-069e9d6b` → **Architecture**. Check that the components and evidence make sense for that repo, and look at the unclassified ones. Then do the same for `sample-repo-dc690514`. These steps only read from Aura.
3. **Tours:** start **Project overview**, then **Entry point**, then a **Component** tour from the Architecture tab. Use Next / Previous and **Explain** on a symbol step.
4. **Search:** try a few natural-language queries for Charlie and open a result's explanation.
5. **One live LLM call** each for **Narrate** and **Generate LLM interpretation**. These send the tour steps or the symbol's facts (and, for local projects, a verified excerpt) to Gemini.

Nothing in Phase 2 writes to Aura.
