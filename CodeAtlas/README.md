# CodeAtlas (Python MVP)

A Neo4j-first repository knowledge graph, inspired by Understand-Anything (the original
TypeScript implementation is untouched). It analyzes a local **Python** repository with
tree-sitter, persists a project-scoped code graph in **Neo4j Aura**, visualizes graphs
returned by Cypher in **Streamlit**, and answers questions grounded in fixed Cypher patterns.

```
Streamlit (ui/app.py) ──HTTP──► FastAPI (codeatlas/api/main.py)
                                   │
   scanner → extractor (tree-sitter) → resolver → sync (batched MERGE + sweep) ──► Neo4j Aura
                                   └── queries (read catalog) / qa (facts → GPT-4o mini) ◄──┘
```

## Graph model

| Node | Key | Notable properties |
|---|---|---|
| `Project` | `id` (= `project_id`) | `name`, `root_path`, `source='codeatlas'`, `sync_status`, `last_run_id` |
| `File` | `id` | `path`, `module`, `sha256`, `loc`, `docstring`, `has_syntax_errors` |
| `Class` | `id` | `qualname`, `file_path`, `start_line`, `end_line`, `bases`, `docstring` |
| `Function` | `id` | `qualname`, `file_path`, `start_line`, `end_line`, `is_method`, `params`, `unresolved_calls`, `external_calls` |
| `Module` | `id` | `name`, `kind` (`external` / `unresolved`) |

Every node has `project_id`; ids are `"{project_id}:{kind}:{path}[:{qualname}]"`, and
`project_id = slug(name) + sha1(realpath)[:8]`, so different repositories never collide.

| Relationship | Evidence kept on the relationship |
|---|---|
| `(Project)-[:CONTAINS]->(File)` | — |
| `(File)-[:DEFINES]->(Class\|Function)` | every symbol has exactly one |
| `(Class)-[:HAS_METHOD]->(Function)` | — |
| `(File)-[:IMPORTS]->(File\|Module)` | one per (file, target): `lines[]`, `names[]`, `count`, `resolution` |
| `(Class)-[:INHERITS]->(Class)` | `confidence` |
| `(Function)-[:CALLS]->(Function)` | one per (caller, callee): parallel `lines[]`, `confidences[]`, `resolutions[]`, plus `count`, `confidence` (best) |

`CALLS` confidence: `exact` = local/nested def, same-file def, imported name, module attribute,
`self`/`cls` method (incl. resolved base classes), constructor. `name_match` = the only
project-wide top-level definition with that name. Ambiguous, builtin, external and
unknown-receiver calls (`obj.method()`) create **no** edge; they are listed on the function.
There is no separate `DEPENDS_ON`: file dependency *is* `IMPORTS`.

## Sync semantics (safe re-analysis)

Each run gets a `run_id`: lock the project (`sync_status='writing'`) → batched MERGE of all
nodes/relationships stamped with `last_run_id` → **only if every batch succeeded**, sweep this
project's items with an older stamp (`sweeping`) → `complete`. A failed run leaves a *mixed*
graph (some items updated, stale items not yet removed) flagged `failed`; the UI and Q&A warn
about it. Re-running converges to the exact current source state. Every write/delete is
anchored on this project's `id`/`project_id`; other projects and unrelated data are untouched.

## Layout

```
<workspace>/            .venv/ (shared environment)   .env (credentials, never committed)
  CodeAtlas/            pyproject.toml  uv.lock  .env.example
    src/codeatlas/      analysis/ api/ graph/ llm/ qa/  cli.py config.py models.py ids.py
    ui/app.py           Streamlit UI
    tests/              unit tests, fixtures/sample_repo, opt-in Neo4j integration tests
    cypher/             aura_examples.cypher
    docs/               RUN_AND_VERIFY.md (step-by-step run + Streamlit + Aura guide)
```

Settings come from `CODEATLAS_ENV_FILE` if set, else `CodeAtlas/.env`, else the workspace `.env`.

## Run

Full walkthrough: **[docs/RUN_AND_VERIFY.md](docs/RUN_AND_VERIFY.md)**. Short version, using the
shared workspace `.venv` (no environment inside `CodeAtlas/`):

```bash
cd <workspace>/CodeAtlas && source ../.venv/bin/activate
python -m pytest                                                   # offline unit tests
python -m codeatlas.cli analyze tests/fixtures/sample_repo --dry-run   # no DB access
python -m codeatlas.cli analyze tests/fixtures/sample_repo             # WRITES to Aura
uvicorn codeatlas.api.main:app --port 8000                         # terminal 1
streamlit run ui/app.py                                            # terminal 2
```

Dependencies are managed with `uv` against the shared environment:
`uv add --active <pkg>` / `uv sync --active --frozen` (run from `CodeAtlas/` with `../.venv`
activated). `--active` makes uv use the activated workspace `.venv` instead of creating
`CodeAtlas/.venv`.

## Aura

`cypher/aura_examples.cypher` has graph-view queries: project → files, files → symbols,
import graph, shortest dependency path, call chains, neighbourhoods, file change impact and
statistics. Set `:param pid => '<project id>'` first.

## Limits (AuraDB Free)

Free tier: 200k nodes / 400k relationships, 1 GB, auto-pause after 72 h idle (resume in the
console before a demo). CodeAtlas refuses projects above 1,500 Python files, 25k nodes or 60k
relationships, and skips files above 300 KB. Writes are batched (500 rows), queries have
timeouts (30 s), traversals are capped (depth ≤ 3, paths ≤ 6 hops, ≤ 400 nodes per view).

## Not covered yet

TypeScript/JavaScript, LLM file summaries, embeddings/vector search, dynamic imports,
type inference for `obj.method()` calls.
