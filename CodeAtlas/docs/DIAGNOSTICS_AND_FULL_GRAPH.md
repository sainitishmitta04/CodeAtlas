# LLM errors, the complete-graph view, Neo4j warnings and honest status

This document covers what changed, why, how to check it yourself, and what is still unverified.
For general run instructions, see [RUN_AND_VERIFY.md](RUN_AND_VERIFY.md).

All of this was checked with **offline tests only**. No live LLM request, GitHub request, Aura read or write, or server restart was made.

---

## 1. LLM failures (`/ask` was returning 200 when the provider said 403)

### What the code shows (confirmed)

- **Configuration is loaded and used as written.**
  - `config.py` reads the workspace-root `.env` (`/Users/nitish/Downloads/Neo4j/.env`).
  - The values used are `OPENAI_API_KEY`, `OPENAI_BASE_URL` (`https://graphacademy.neo4j.com/api/llm/v1`) and `OPENAI_MODEL` (`gpt-4o-mini`).
  - No shell variables override them. I checked this by comparing values in memory only; nothing was printed.
- **The request is built correctly.** The OpenAI SDK sends `POST {OPENAI_BASE_URL}/chat/completions` with `Authorization: Bearer <key>` and the configured model. That is exactly the URL in your log.
  - No bug in the URL, path, header or model was found.
  - The endpoint and model are unchanged.
- **The actual code defect:** `qa/answer.py` caught every LLM failure and returned fallback text with HTTP **200**. The UI then displayed that text where an answer normally goes.
- **Diagnostics were thrown away.** `llm/client.py` logged only the exception type, so the provider's status, message and request id were lost.

### Not confirmed: why the provider refuses (403)

- 403 means the provider received the request and refused it.
- The workspace has no documentation describing this GraphAcademy endpoint.
- Possible reasons:
  - the credential isn't entitled to this endpoint, for example an inactive course or workshop, or an expired key;
  - the model isn't allowed for this credential;
  - a gateway or firewall rule.
- A 403 does **not** show that `.env` is wrong.
- The new diagnostics show the provider's redacted message and request id on the first real question. That is what distinguishes these causes; see §6.

### What changed

| Area | Change |
|---|---|
| `.env` precedence | The workspace `.env` is now the source of truth. A stale exported variable (e.g. an old `OPENAI_API_KEY`) can no longer override it; shell variables only fill settings that `.env` does not define. `/health` and the sidebar list the *names* (never values) of any shell variables that were ignored. |
| Typed errors | `LLMError.code` is one of: `not_configured`, `auth_failed` (401), `permission_denied` (403), `not_found` (404), `bad_request` (400/422), `rate_limited` (429), `timeout`, `unreachable`, `provider_error` (5xx), `bad_response` (malformed, no choices, or empty content, with its `finish_reason`). |
| Error details | Each error carries `provider_status`, a **redacted** `provider_message` (JSON error code, type and message, or a short excerpt of an HTML gateway page), the provider `request_id` (`x-request-id` / `request-id` / `cf-ray`), `attempts`, and an actionable `hint`. |
| Redaction | The configured key, `Bearer …`, `token …`, `api_key=…` and any long token-like string are removed from logs and responses. URLs stay readable. |
| Retries | The SDK's own retries are off (`max_retries=0`), so a provider's `x-should-retry` header can't cause one. CodeAtlas retries **once** (`LLM_MAX_RETRIES`, 0–3), with backoff, and only for 429, 5xx, timeouts and network errors. **401 and 403 are never retried.** |
| `/ask` | Success returns 200 with `kind: "llm"`. If CodeAtlas can't tell which file or function you mean, it returns 200 with `kind: "clarification"`; the LLM is not called. **An LLM failure is an HTTP error:** 503 (not configured), 502 (auth, permission, provider, unreachable or bad response), 429, or 504. The body has `{"component": "llm", "detail": {code: "llm_<code>", message, hint, provider_status, provider_message, request_id, attempts, retryable, graph_context}}`. `graph_context` holds the facts retrieved from Neo4j, with `answer: ""`. |
| Q&A pipeline | Graph retrieval and the prompt are unchanged. Only graph facts are sent to the LLM, never source code. |
| UI (Ask tab) | On failure: a red **No answer: the LLM request failed** with the code and provider status, the hint, and a *Diagnostics* expander (no credentials). Below that, the graph facts and evidence graph, labelled *not an LLM answer*. On success: the answer, with a caption saying it was written by the LLM from N graph facts. |

---

## 2. Complete-graph view

### Why the graph was incomplete (confirmed from the code)

1. The UI's default node types left out `Module`.
2. The overview returned at most 150 nodes (the hard cap was 400), ranked by degree. Isolated nodes ranked last, so they were the first to be cut.
3. Relationships were kept only when both endpoints were in that limited set. Edges beyond the limit, or to a filtered-out type, vanished with no indication.

Serialization and the payload builder were not at fault.

### How it works now

- **Paging.** `GET /graph/overview?project_id=…&labels=…&rel_types=…&limit=<page size>&after=<cursor>` returns one page.
  - Nodes come in a fixed order: by `id`, which is unique and prefixed with the project ID.
  - Each page contains:
    - the next `limit` nodes with the selected labels whose `id` is greater than `after`;
    - every relationship of a selected type between a node on this page and a selected-label node on this page or an earlier one.
  - So every relationship is delivered **exactly once** over a full traversal: on the page that loads its second endpoint.
  - The neighbouring node is returned too, so an edge is never drawn with a missing end.
- **Response fields:**
  - `next_cursor`: send it as `after` to get the next page; `null` means the selection is complete.
  - `complete`, `page_nodes`.
  - On the first page, `totals`:
    - `nodes` and `relationships` for the project;
    - `nodes_by_label`;
    - `selected_nodes` and `selected_relationships` for the current filters;
    - `hidden_relationships`: relationships of a selected type whose other endpoint's type is filtered out.
- **Project scoping:** every node branch is anchored on the project (`{id: $pid}` for Project, `{project_id: $pid}` otherwise). The other endpoint must have the same `project_id`. The totals query is anchored at both ends.
- **Defaults:** all five labels and all six relationship types. The page size is 500, with a maximum of 2,000 per request. `limit` keeps its name for compatibility, but now means page size.
- **UI:**
  - All node types are selected by default, and changing a filter reloads the graph.
  - Pages load automatically up to 2,000 nodes (`CODEATLAS_AUTO_LOAD_NODES`). Past that, **Load next page** and **Load all remaining** are explicit clicks, because laying out very large graphs in the browser is slow.
  - The status line shows either ✅ *Complete graph* or ⚠️ *Partial view*, with displayed vs selected counts for nodes and relationships, how many nodes remain, the project totals, and the hidden-relationship count.
  - "Show only this neighbourhood" is labelled as a neighbourhood view, not the full graph.
  - Expand, remove, selection, project switching and the other tabs are unchanged.
- **Cypher syntax:** `overview_query` and `FIND_ENTITIES` now use `CALL () { … }`, replacing the deprecated `CALL { … }`.
  - Both subqueries are uncorrelated (no outer variables), so the results are identical.
  - That the syntax is supported by your Aura version is inferred from the deprecation warning itself, which Neo4j only emits on versions that support the replacement. It has **not** yet been executed on Aura.

### Viewing a project's complete graph in Aura

See §5 of [RUN_AND_VERIFY.md](RUN_AND_VERIFY.md).

The query you supplied is correct for this schema. Every code node has `project_id`, and the Project node has `id = project_id`. A shorter equivalent is also documented there, and in `cypher/aura_examples.cypher` (query 00):

```cypher
MATCH (n) WHERE n.project_id = $pid
OPTIONAL MATCH (n)-[r]->(m) WHERE m.project_id = $pid
RETURN n, r, m
```

---

## 3. Neo4j warnings

| Warning | Cause | Status |
|---|---|---|
| `sync_error` "property key does not exist" (`/projects`) | `LIST_PROJECTS` named `p.sync_error`. That key only exists in the database after a failed sync, so the notice never went away. | **Fixed in code.** The query returns `properties(p)` and Python picks the fields, so the response shape is unchanged. |
| `source_kind`, `source_label` (`/projects`) | The same query named keys that older databases don't have. | **Fixed in code**, the same way. |
| `source_identity` (GitHub/ZIP identity lookup) | `PROJECTS_BY_IDENTITY` named `p.source_identity`. | **Fixed in code** with dynamic access, `p[$identity_key] = $identity`: the same comparison. Old projects still never match, and new GitHub and ZIP projects still do. |
| `CALL { … }` deprecation | The overview and the entity lookup used the old syntax. | **Fixed** with `CALL () { … }`, as described in §2. |
| `IF NOT EXISTS` constraint and index notices | `ensure_schema` before a sync. | **Left as is.** These are informational, and the schema objects are not recreated. |
| `coalesce($x, p.source_*)` in the sync begin query | This is a write query that keeps existing source fields during CLI runs. | **Left as is.** It can only emit a notice on a database where the key never existed. Your database already has these keys from the Charlie sync. |

**Still to verify live:** the claim that `properties(p)` and `p[$key]` stop the notices is based on how Neo4j decides when to emit them, and has not been observed on Aura. A read-only `EXPLAIN` would confirm it, and is waiting for your approval.

**Nothing changed in the data:** no migration, no backfill, no resync, and no new database.

**Source metadata lifecycle (tested):**
- Begin sets the `source_*` fields with `coalesce`, so a CLI or local run never erases them.
- Finalize sets them again for GitHub and ZIP sources.
- A failure records `sync_error` and the phase only, and never touches `source_*`.
- CLI `analyze` and `--dry-run` behave exactly as before.

---

## 4. Honest status

- **`/health`** pings Neo4j (as before) and **never calls the LLM**. It now adds:
  - `llm: {configured, model, base_url_host, base_url_valid, last_call}`. `last_call.state` is `unknown` until a real `/ask`, then `ok` or `failed` with the `code`, `provider_status`, `request_id` and time.
  - `config: {env_file_found, shell_vars_ignored_in_favour_of_env_file}`, which contains names only.
- **Sidebar:**
  - ⚪ *configured, not verified yet*;
  - 🟢 *last call OK*;
  - 🔴 *last call failed: permission_denied (HTTP 403)*;
  - ⚪ *not configured*.

  Having a key configured is no longer shown as green.
- **Error bodies** now include `component`: `neo4j`, `llm`, `github`, `source` or `api`. The UI prefixes errors with it, for example `[neo4j] Neo4j is unreachable…`. "Backend not reachable" still identifies the local API.
- **Logging:** a failed LLM call logs `code`, `provider_status`, `request_id`, `attempts`, the host, the model and the redacted provider message. Values and credentials are never logged.

---

## 5. Files changed

| File | Why |
|---|---|
| `src/codeatlas/config.py` | `.env` takes precedence over shell variables; `shadowed_env_names()` (names only); `llm_max_retries` |
| `src/codeatlas/llm/client.py` | Typed `LLMError`, redaction, provider message and request id, bounded retries, `status_snapshot()`, `config_summary()`, client rebuilt when the configuration changes |
| `src/codeatlas/qa/answer.py` | `AnswerFailed` carries the retrieved facts; the `kind="clarification"` reply; no more success-shaped fallback |
| `src/codeatlas/models.py` | `Answer.kind`; paging fields on `GraphPayload` (`next_cursor`, `complete`, `page_nodes`, `totals`) |
| `src/codeatlas/graph/queries.py` | Paged `overview_query` with `CALL () {}`; `overview_totals()` and the per-variant count query; `FIND_ENTITIES` uses `CALL () {}`; warning-safe `LIST_PROJECTS` and `PROJECTS_BY_IDENTITY` |
| `src/codeatlas/api/main.py` | `/ask` failure handler; `component` on errors; `/health` LLM and config sections; overview `after` and `include_totals` parameters |
| `src/codeatlas/cli.py` | Catalog includes the new totals query, so the static Cypher checks cover it |
| `src/codeatlas/sources/errors.py`, `src/codeatlas/sources/service.py` | `component` on source errors; GitHub failures tagged `github` |
| `ui/app.py` | LLM status line; all labels on by default; paging loop and completeness indicators; neighbourhood-view label; Ask tab error handling; component prefixes on errors |
| `docs/RUN_AND_VERIFY.md` | Complete-graph Aura query and how to find the project ID; updated Streamlit checklist and troubleshooting |
| `cypher/aura_examples.cypher` | Query 00: the complete project graph |
| `docs/DIAGNOSTICS_AND_FULL_GRAPH.md` | This document |
| **New tests:** `tests/test_llm_client.py` (29), `tests/test_ask_api.py` (16), `tests/test_overview_paging.py` (12), `tests/test_project_metadata.py` (13) | §7 |

**Not touched:**
- `.env`, `.gitignore`, `data/`, `Understand-Anything/`, `.git`;
- `pyproject.toml`, `uv.lock`, and installed packages;
- Aura data;
- the ingestion pipeline, write confirmation, identity rules, and sync/sweep logic.

---

## 6. Manual verification (each step needs your approval first)

1. **Restart the backend**, then Streamlit. From `CodeAtlas/`, in two terminals:

   ```bash
   # Terminal 1: stop the running uvicorn with Ctrl-C, then
   source ../.venv/bin/activate && uvicorn codeatlas.api.main:app --port 8000
   # Terminal 2: stop Streamlit with Ctrl-C, then
   source ../.venv/bin/activate && streamlit run ui/app.py
   ```

   Then check the health response. It must show `llm.last_call.state = "unknown"` and an empty `shell_vars_ignored…` list:

   ```bash
   curl -s localhost:8000/health | python -m json.tool
   ```

2. **Compare totals for Charlie.**

   ```bash
   curl -s 'localhost:8000/graph/overview?project_id=charlie-069e9d6b&limit=2000' \
     | python -c "import json,sys; d=json.load(sys.stdin); print(d['totals'], d['complete'], len(d['nodes']), len(d['edges']))"
   ```

   In the UI, select Charlie. The status line should show ✅ *Complete graph*, with displayed counts equal to the selected totals (unless Charlie has more than 2,000 nodes, in which case use **Load all remaining**). All five node types should be ticked, including Module.

3. **Aura.** Run `:param pid => 'charlie-069e9d6b'` and then query 00, or your original query, in the graph view. The node and relationship counts should match `totals.nodes` / `totals.relationships`. Raise the display limit if the graph looks cut off.

4. **Ask one question** in the UI, for example *What does this repository do?* This is one live LLM request.
   - If it works: an answer appears, the sidebar shows 🟢 *last call OK*, and the caption says it was written by the LLM.
   - If it fails: you get the red error. Open *Diagnostics* and note `code`, `provider_status`, `request_id` and `provider_message`. The uvicorn terminal logs one line: `LLM call failed: code=… provider_status=… request_id=…`.

5. **If you still get a 403:**
   - Read the redacted `provider_message`. It may name the reason directly, for example a model, quota, course or expired-key message. An HTML page suggests a gateway or firewall.
   - Then confirm in your GraphAcademy account that the key in `.env` is still valid for `graphacademy.neo4j.com/api/llm/v1`, and that `gpt-4o-mini` is permitted for it. Give the provider the request id if they need it.
   - Don't share the key in chat. If it has to be replaced, edit `.env` yourself and restart the API.

6. **Check the sample project is untouched.** Run `python -m codeatlas.cli integrity sample-repo-dc690514` (read-only; expect all `0`), and run query 6 in Aura with `pid = 'sample-repo-dc690514'`. Expect Project 1, File 8, Module 2, Class 2, Function 15.

Also waiting for approval:
- a read-only `EXPLAIN` of the changed queries on Aura, to confirm the syntax is accepted and the notices are gone;
- `pytest -m neo4j`, which writes `test-*` projects to Aura and then deletes them.

---

## 7. Offline tests

Run them from `CodeAtlas/` with `source ../.venv/bin/activate && python -m pytest`.
Result: **368 passed, 4 deselected**. The 4 are the opt-in `-m neo4j` Aura tests, which were not run.

- **`test_llm_client.py` (29), run through the real OpenAI SDK with a mocked transport and fake keys:**
  - `.env` beats shell variables, and shell variables fill gaps;
  - the request uses the configured URL, Bearer key and model;
  - the config summary has no key material;
  - "not configured" and an invalid base URL make no request;
  - the client is rebuilt when the configuration changes;
  - 403 (JSON and HTML), 401, 404, 400, 429 and 500/503: status mapping, which ones are retried, and the attempt count;
  - transient failure followed by success; retries disabled; timeout; connection failure;
  - four malformed-response cases;
  - no secret appears in errors or logs; redaction cases; URLs stay readable.
- **`test_ask_api.py` (16):**
  - success;
  - seven failure types, each mapped to a non-200 status with `graph_context`;
  - 403 details and no retry;
  - 503 when not configured;
  - clarification returns 200 without calling the LLM;
  - an unknown project returns 400;
  - `/health` never contacts the provider and reports `last_call`;
  - the Streamlit Ask tab shows the error and not an answer, shows a success correctly, and the sidebar doesn't call an unverified LLM healthy.
- **`test_overview_paging.py` (12):**
  - an in-memory graph with more than 150 nodes, all five labels, all six relationship types, isolated nodes, a second project, and a planted cross-project edge;
  - paging returns every node and every relationship **exactly once**;
  - single page; totals and hidden counts; consistent label and type filters; clamping and validation;
  - the Cypher's shape; no deprecated `CALL {` in the catalog;
  - an API round trip; the defaults;
  - the Streamlit status goes from partial to complete, and hidden relationships are reported.
- **`test_project_metadata.py` (13):**
  - read queries never name optional keys;
  - project lists with and without source metadata, including a failed project;
  - identity lookup semantics;
  - source metadata through a CLI sync, a source sync, and a failed sync;
  - CLI `--dry-run` unchanged, with no database access.
- **The earlier tests all still pass:** the local, GitHub and ZIP sources, the write guard, the Streamlit ingest flow, the query catalog, the resolver, and so on.

**About the paging tests:** they emulate the documented page rule in Python over real `neo4j.graph` objects. The Cypher text is checked statically but has not been executed. Running it on Aura is part of the approval-gated verification in §6.

---

## 8. Next-phase roadmap (proposed, not implemented)

Ordered so that demo reliability comes before new features.

| # | Item | User value | Complexity | Risks | Depends on | How to verify |
|---|---|---|---|---|---|---|
| 1 | **Resolve LLM access and rehearse the demo** | Q&A actually works on demo day | Low (account side) | The provider may not grant access in time | Your GraphAcademy account | One approved live question returns 200 and the sidebar shows 🟢 |
| 2 | **Live read-only check of the changed Cypher** (`EXPLAIN`, plus one overview call on Charlie) | Confirms the `CALL () {}` syntax, the warning fixes and the paging totals on real data | Low | None (read-only) | Your approval | No notifications from `EXPLAIN`; UI totals match the Aura counts |
| 3 | **Polished demo script** (fixture → Charlie → full graph → impact → Ask, with the Aura query side by side) | A smooth 5-minute story | Low | Aura auto-pauses after 72 h idle | Items 1–2 | Timed rehearsal |
| 4 | **Answer citations**: link each claim to the file and line facts it used; show "insufficient evidence" explicitly | More trustworthy Q&A | Medium | Prompt drift | Item 1 | Tests that every cited path exists in the evidence |
| 5 | **Update preview / change summary** before a write: which files would be added, removed or changed (compare file hashes against the stored File nodes) | Safer updates; a stronger story for the write confirmation | Medium | One extra read query per write | — | Offline tests with a fake read layer; one live dry run |
| 6 | **Incremental analysis**: re-parse only files whose hash changed; reuse the sweep | Faster re-syncs of large repos | Medium–high | Cross-file resolution must still be re-run | Item 5 | Re-sync with no changes writes nothing; integrity checks stay 0 |
| 7 | **Server-side progressive expansion for very large graphs** (cluster by directory or package, expand on demand) | Usable views beyond about 5,000 nodes | Medium | UI complexity | Existing paging | Performance benchmark on a large repo |
| 8 | **Structured logs and benchmarks**: JSON logs with a request id spanning API → Neo4j → LLM; timing for scan, parse and sync | Faster diagnosis | Low–medium | Log volume | — | Log-shape tests; benchmark script |
| 9 | **Modular parsers (TypeScript/JavaScript next)** behind a language-plugin interface | Analyze the original Understand-Anything code | High | Must not change Python results | Installing `tree-sitter-typescript`, which needs your approval | The Python golden tests stay identical; new TypeScript golden fixtures |
