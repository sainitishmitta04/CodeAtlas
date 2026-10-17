# CodeAtlas: Run & Verify Guide

A single reference covering how to run CodeAtlas, how to check the result in **Streamlit**, and how to
look at the same graph in the **Neo4j Aura console**.

> ⚠️ Steps marked **WRITES** change data in your Aura instance. Every write is scoped to a single
> CodeAtlas project (`project_id`). Unrelated data is never touched.

---

## 0. Layout and prerequisites

```
Neo4j/                       ← workspace
├── .venv/                   ← shared Python environment (reuse it; never create another)
├── .env                     ← credentials (Neo4j + OpenAI-compatible API); never commit, never print
├── Understand-Anything/     ← original TypeScript project (untouched)
├── data/                    ← unrelated files (Aura credential download, old CSV/JSON exports)
└── CodeAtlas/               ← this application
    ├── src/codeatlas/       analysis/ api/ graph/ llm/ qa/ cli.py config.py …
    ├── ui/app.py            Streamlit UI
    ├── tests/               unit tests + fixtures/sample_repo + opt-in integration tests
    ├── cypher/aura_examples.cypher
    └── docs/RUN_AND_VERIFY.md   (this file)
```

You need:

- **The workspace `.env`** with these variable names: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`,
  `NEO4J_DATABASE`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`. Where settings are read
  from, in order:
  1. `$CODEATLAS_ENV_FILE`, if set
  2. `CodeAtlas/.env`
  3. the workspace `.env`
- **A running Aura instance.** AuraDB Free pauses itself after 72 h of inactivity; if it is
  paused, open <https://console.neo4j.io> and click **Resume**.

Every terminal starts the same way:

```bash
cd ~/Downloads/Neo4j/CodeAtlas
source ../.venv/bin/activate
```

---

## 1. Offline checks (no database, no LLM)

```bash
python -m pytest                                                     # expect: 160 passed, 4 deselected
python -m codeatlas.cli analyze tests/fixtures/sample_repo --dry-run # parse only, prints a JSON report
```

The dry-run report for the fixture should contain:

| Node counts | Relationship counts |
|---|---|
| Project 1, File 8, Module 2, Class 2, Function 15 | CONTAINS 8, DEFINES 17, HAS_METHOD 5, IMPORTS 7, INHERITS 1, CALLS 13 |

Check Neo4j connectivity (read-only):

```bash
python -c "from codeatlas.graph import db; print(db.ping())"         # expect {'ok': True}
```

---

## 2. Load a repository into Aura (**WRITES**)

```bash
python -m codeatlas.cli analyze tests/fixtures/sample_repo           # small demo repo
python -m codeatlas.cli analyze .                                    # CodeAtlas itself (~40 files)
python -m codeatlas.cli analyze /path/to/any/python/repo --name my-repo
```

- **First run:** it also creates the `codeatlas_*` constraints and indexes, using
  `IF NOT EXISTS`, so repeating it is safe.
- **Output:** a JSON report. Note the `project_id` (for example `sample-repo-dc690514`; the
  suffix depends on the absolute path), `"sync_status": "complete"`, and the swept counts.
- **Re-running:** analyzing the same path again is idempotent. Counts stay the same. Deleted
  or changed code is removed from the graph (`swept_nodes` / `swept_rels`).

Check graph integrity. Every value should be `0`:

```bash
python -m codeatlas.cli integrity <project_id>
```

Remove a single project (it only touches nodes with that `project_id`):

```bash
python -m codeatlas.cli delete-project <project_id> --yes
```

---

## 3. Start the app (two terminals)

```bash
# Terminal 1 — API (http://127.0.0.1:8000, interactive docs at /docs)
uvicorn codeatlas.api.main:app --port 8000

# Terminal 2 — UI (opens http://localhost:8501)
streamlit run ui/app.py
```

If the API runs somewhere else, set `CODEATLAS_API_URL` before starting Streamlit.

Quick API checks (read-only):

```bash
curl -s http://127.0.0.1:8000/health          # neo4j ping + llm {configured, model, base_url_host, last_call}
curl -s http://127.0.0.1:8000/projects        # list of CodeAtlas projects
```

---

## 4. Check it in Streamlit

| Where | What to do | What you should see (fixture repo) |
|---|---|---|
| Sidebar header | — | 🟢 Neo4j. LLM shows ⚪ *configured, not verified yet* until a question is asked, then 🟢 *last call OK* or 🔴 *last call failed: <code>*. `/health` never calls the LLM. |
| Sidebar → **Analyze repository** | Pick a **Source** (Local Directory / GitHub URL / ZIP Upload), fill it in, then **Dry Run (no database writes)** or **Analyze & Write to Neo4j Aura**. A write first shows the target project and needs the confirmation box ticked. See [REPOSITORY_SOURCES.md](REPOSITORY_SOURCES.md). | A readable result panel above the tabs: source, project id, files/nodes/relationships, resolution, skipped files, raw JSON under *Diagnostics* (**WRITES** only after the confirmed write) |
| Sidebar → **Project** | Select `sample_repo (sample-repo-…)` | Warning banner only if `sync_status ≠ complete` |
| Sidebar → **Filters** | Node types (all five selected by default, including Module), relationship types, nodes per page, expansion depth | Changing a filter reloads the graph. A relationship is drawn when both endpoint types are selected; the rest are counted as *hidden* |
| **Graph** tab → status line | — | ✅ *Complete graph* with "Displayed X nodes / Y selected · A relationships / B selected", or ⚠️ *Partial view* with **Load next page** / **Load all remaining** (pages load automatically up to 2,000 nodes, `CODEATLAS_AUTO_LOAD_NODES`). Also the project totals and how many relationships are hidden by the type filter. |
| **Graph** tab | Zoom/pan, click a node, double-click it (or use the toolbar *expand*) | Colours: Project dark, File blue, Class purple, Function green, Module grey. Labelled, directed edges. The right panel shows properties (path, line range, docstring, `unresolved_calls`). Expanding loads neighbours from Neo4j. |
| **Graph** tab → *Cypher used* | Expand it | The exact Cypher and parameters, which you can paste into Aura |
| **Statistics** tab | — | Node/relationship counts, CALLS by confidence (`exact` / `name_match`), IMPORTS by resolution, most imported files, most called functions. **Run graph integrity checks** → all `0`. |
| **Paths & impact** → Dependency path | From `shop/cli.py`, to `shop/models.py` → **Find path** | `cli.py -[IMPORTS]-> models.py` (if there's no path, it tries the reverse direction and says so) |
| **Paths & impact** → Change impact | Changed file `shop/models.py` → **Analyze impact** | **Files importing it:** `shop/cli.py`, `shop/pricing.py`. **Functions calling into it:** `make_items`, `make_items.build`, `main`. **Subclasses:** none outside the file. Plus a list of what the analysis cannot see. |
| **Architecture** tab | — | Inferred layers (metrics + Graphviz diagram), components with confidence and evidence, possible violations, cycles; *Inspect a component* → Component / Dependency / Impact tour. See [ARCHITECTURE_INTELLIGENCE.md](ARCHITECTURE_INTELLIGENCE.md) |
| **Tours** tab | Pick a tour kind and target → **Start tour** | Ordered steps with layer, location, evidence and observed/inferred flags; Previous / Next; **Explain** a symbol step inline. *Narrate (LLM)* is optional |
| **Explain** tab | Find a symbol by name (or use the selected graph node) → **Explain** | Observed facts, unknowns, verified source excerpt (local projects); **Generate LLM interpretation** is optional |
| **Search** tab | Describe what you're looking for | Ranked symbols/files with location, layer and *why it matched*. Lexical + graph search, not embeddings |
| **Ask** tab | Click an example or type a question → **Ask** | An answer with **Known from the graph** / **Hypotheses** sections, cited paths, caveats, the evidence facts, an evidence graph, and the Cypher used. If the LLM call fails you get a red **No answer: the LLM request failed** error with the code, the provider's HTTP status, a hint and a *Diagnostics* expander (request id, redacted provider message); the facts retrieved from Neo4j are shown underneath, labelled *not an LLM answer*. |

Good questions to try:

- *What does this repository do?*
- *Which files contain the main functionality?*
- *What depends on `shop/utils.py`?*
- *Which functions are connected to `total`?*
- *What could be affected if `shop/models.py` changes?*

Tip: select a node in the Graph tab first. Questions with no explicit name use it as the focus.

---

## 5. Check it in the Neo4j Aura console

1. Open <https://console.neo4j.io>, pick your instance, and click **Query** (or open Neo4j
   Browser). Connect with the same credentials as in `.env`.
2. Results that return nodes, relationships or paths render as a **graph**. Use the
   Graph/Table toggle to switch views.
3. Set the project parameter **once per session**. Get the id from query 0, then run:

```
:param pid => 'sample-repo-dc690514'
```

(Use your own id. If `:param` isn't available, replace `$pid` with the quoted id in each query.)

All of these are in `cypher/aura_examples.cypher` and are read-only.

**Complete graph of one project** (graph view). Every File, Class, Function and Module node
carries the project's id in `project_id` (set by the sync for each node), and the Project node
has both `id` and `project_id` equal to it. So this read-only query returns every node and
every relationship of the project, and nothing from other projects:

```cypher
MATCH (p:Project {id: 'charlie-069e9d6b'})
OPTIONAL MATCH (n)
WHERE n = p OR n.project_id = p.id
WITH collect(DISTINCT n) AS nodes
UNWIND nodes AS a
OPTIONAL MATCH (a)-[r]-(b)
WHERE b IN nodes
RETURN DISTINCT a, r, b
```

Equivalent, shorter and cheaper (each relationship is matched once, in its own direction,
and there's no list lookup per relationship):

```cypher
MATCH (n) WHERE n.project_id = $pid
OPTIONAL MATCH (n)-[r]->(m) WHERE m.project_id = $pid
RETURN n, r, m
```

- **Find the project id:** it's in brackets after the name in the Streamlit **Project**
  selector, in `GET /projects`, or from query 0 below. Then `:param pid => 'charlie-069e9d6b'`.
- Neo4j Browser / Aura Query draw at most a few hundred nodes by default. For big projects,
  raise the limit in the Query/Browser settings (Browser: `:config initialNodeDisplay: 5000`),
  or check the totals with query 6 / 6b first.
- `MATCH (n) WHERE n.project_id = …` scans all nodes. That's fine on AuraDB Free (at most
  200k nodes); the app itself uses label-anchored, index-backed queries.

**0. Which CodeAtlas projects exist** (table)
```cypher
MATCH (p:Project {source: 'codeatlas'})
RETURN p.id AS id, p.name AS name, p.sync_status AS status, p.file_count AS files, p.synced_at AS synced;
```

**1. Project → files** (graph)
```cypher
MATCH (p:Project {id: $pid})-[r:CONTAINS]->(f:File)
RETURN p, r, f LIMIT 50;
```

**2. Files → classes/functions, plus class methods** (graph)
```cypher
MATCH (f:File {project_id: $pid})-[d:DEFINES]->(s)
OPTIONAL MATCH (s)-[m:HAS_METHOD]->(fn:Function)
RETURN f, d, s, m, fn LIMIT 150;
```

**3. Import graph** (graph; external packages show up as `Module` nodes)
```cypher
MATCH (a:File {project_id: $pid})-[i:IMPORTS]->(b)
RETURN a, i, b LIMIT 150;
```

**3b. Internal file-to-file dependencies only**
```cypher
MATCH (a:File {project_id: $pid})-[i:IMPORTS]->(b:File)
RETURN a, i, b LIMIT 150;
```

**4. Multi-hop dependency path** (shortest IMPORTS chain)
```cypher
MATCH (a:File {project_id: $pid, path: 'shop/cli.py'})
MATCH (b:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = shortestPath((a)-[:IMPORTS*1..6]->(b))
RETURN p;
```

**4b. Everything a file depends on (up to 3 hops)**
```cypher
MATCH p = (f:File {project_id: $pid, path: 'shop/cli.py'})-[:IMPORTS*1..3]->(:File)
RETURN p LIMIT 50;
```

**4c. Call chains reaching a function** (click a CALLS edge to see `lines`, `confidences`, `count`)
```cypher
MATCH p = (:Function {project_id: $pid})-[:CALLS*1..3]->(fn:Function {project_id: $pid, name: 'fmt'})
RETURN p LIMIT 50;
```

**5. A component and its neighbourhood (2 hops, bounded)**
```cypher
MATCH (c:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = (c)-[*1..2]-(x)
WHERE x.project_id = $pid
RETURN p LIMIT 80;
```

**5b. Change impact of a file:** importers (file level) plus callers of its functions (function level)
```cypher
MATCH (f:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = (:File)-[:IMPORTS*1..2]->(f)
RETURN p
UNION
MATCH (f:File {project_id: $pid, path: 'shop/models.py'})
MATCH p = (:Function)-[:CALLS*1..2]->(:Function)<-[:DEFINES]-(f)
RETURN p;
```

**6. Node counts by type** (table)
```cypher
MATCH (n:File {project_id: $pid}) RETURN 'File' AS label, count(n) AS count
UNION ALL MATCH (n:Module {project_id: $pid}) RETURN 'Module' AS label, count(n) AS count
UNION ALL MATCH (n:Class {project_id: $pid}) RETURN 'Class' AS label, count(n) AS count
UNION ALL MATCH (n:Function {project_id: $pid}) RETURN 'Function' AS label, count(n) AS count;
```

**6b. Relationship counts by type** (table)
```cypher
MATCH (:Project {id: $pid})-[r:CONTAINS]->() RETURN 'CONTAINS' AS type, count(r) AS count
UNION ALL MATCH (:File {project_id: $pid})-[r:DEFINES]->() RETURN 'DEFINES' AS type, count(r) AS count
UNION ALL MATCH (:Class {project_id: $pid})-[r:HAS_METHOD]->() RETURN 'HAS_METHOD' AS type, count(r) AS count
UNION ALL MATCH (:File {project_id: $pid})-[r:IMPORTS]->() RETURN 'IMPORTS' AS type, count(r) AS count
UNION ALL MATCH (:Class {project_id: $pid})-[r:INHERITS]->() RETURN 'INHERITS' AS type, count(r) AS count
UNION ALL MATCH (:Function {project_id: $pid})-[r:CALLS]->() RETURN 'CALLS' AS type, count(r) AS count;
```

**6c. Call evidence quality** (table)
```cypher
MATCH (:Function {project_id: $pid})-[r:CALLS]->(:Function)
RETURN r.confidence AS confidence, count(r) AS edges, sum(r.count) AS call_sites;
```

**7. Whole-database sanity checks** (read-only)
```cypher
SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties WHERE name STARTS WITH 'codeatlas_' RETURN *;
SHOW INDEXES YIELD name, labelsOrTypes, properties, state WHERE name STARTS WITH 'codeatlas_' RETURN *;
CALL db.schema.visualization();       // model overview: labels and relationship types
MATCH (n) RETURN count(n) AS nodes;   // AuraDB Free limit: 200,000
MATCH ()-[r]->() RETURN count(r) AS rels;  // AuraDB Free limit: 400,000
```

**Viewing tips**
- Click a label chip (e.g. `File`) in the result panel to set its colour and caption. Use
  `path` for File, `qualname` for Function/Class, and `name` for Module.
- Click any node or relationship to see its properties: line ranges, docstrings, IMPORTS
  `lines`/`names`/`count`, CALLS `lines`/`confidences`/`resolutions`.
- Keep the `LIMIT`s. Without them, large repositories can overwhelm the view on AuraDB Free.
- **Never** run `MATCH (n) DETACH DELETE n`, because it would wipe unrelated data too. Use
  `python -m codeatlas.cli delete-project <id> --yes`.

---

## 6. Optional: integration tests (**WRITES**)

```bash
python -m pytest -m neo4j
```

These create temporary `test-*` projects and delete only those. They check that:

- ingestion is idempotent,
- removed files are swept,
- failed syncs recover after a retry,
- the fixture queries return the expected graphs.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `Neo4j is unreachable…` / 503 | The instance is paused: resume it in the Aura console. Also check `NEO4J_URI` (`neo4j+s://…`). |
| `Neo4j authentication failed` | Fix `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` in the workspace `.env`. |
| Streamlit says *Backend unreachable* | Start `uvicorn codeatlas.api.main:app --port 8000`, or set `CODEATLAS_API_URL`. |
| `ModuleNotFoundError: codeatlas` | Activate `../.venv`. If it still fails, run `uv sync --active --frozen` from `CodeAtlas/`. |
| uv wants to create `CodeAtlas/.venv` | You forgot `--active`, or `../.venv` isn't activated. |
| Project shows `sync_status: failed` | Re-run the same `analyze` command; the sync converges to the current source. |
| Analysis refused as too large | Analyze a subdirectory. Limits are 1,500 files, 25k nodes and 60k relationships per project (`MAX_FILES`, …). |
| Ask tab: **No answer: the LLM request failed** | Read the code. `llm_not_configured` (503): set `OPENAI_API_KEY` in the workspace `.env`. `llm_auth_failed` (401): the provider did not accept the key. `llm_permission_denied` (403): the request reached the provider with the configured endpoint, model and key, and the provider refused it; check with the provider that this credential is entitled to that endpoint and model. `llm_rate_limited` (429), `llm_timeout` (504), `llm_unreachable` / `llm_provider_error` (502): transient, retried once automatically. The *Diagnostics* expander shows the provider's request id and redacted message to quote to the provider. Graph browsing works regardless. |
| Sidebar lists *Shell variables ignored in favour of the workspace .env* | Those variables are exported in your shell with a different value than `.env`; the `.env` value is used. Unset them (`unset NAME`) to remove the notice. |
| *Confirm the target project* after clicking write | Expected: every write shows its target first. Tick the confirmation box and click **Analyze & Write to Neo4j Aura** again. |
| GitHub: *rate limit reached* (429) | Unauthenticated GitHub API allows 60 requests/hour per IP (each GitHub run uses 2). Wait, or upload the repository as a ZIP. |
| ZIP rejected (*Unsafe path*, *archive bomb*, *too large*) | See the limits in [REPOSITORY_SOURCES.md](REPOSITORY_SOURCES.md); they can be raised in `.env` (`MAX_ARCHIVE_BYTES`, …). |
| Fixture `project_id` changed | It comes from the folder's absolute path. Moving a repo creates a new project; delete the old one with `delete-project`. |
