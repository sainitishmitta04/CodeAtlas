# Repository sources: local directory, GitHub URL, and ZIP upload

This document covers what changed, how to use the three input methods, and why they are built the way they are.
For general run instructions, see [RUN_AND_VERIFY.md](RUN_AND_VERIFY.md).

---

## 1. Summary

CodeAtlas can now analyze a repository from three sources:

| Source | How it is read | Where `root_path` points in Neo4j |
|---|---|---|
| **Local Directory** | Read directly from the folder, as before | The real absolute path, unchanged |
| **GitHub URL** (public repos only) | A GitHub source archive is downloaded into a temporary folder. The `git` program is not used. An optional server-side `GITHUB_TOKEN` raises GitHub's rate limit. | `github.com/Owner/Repo@ref` |
| **ZIP Upload** | The archive is safely extracted into a temporary folder | `zip:<file>.zip (sha256 <12 hex>)` |

All three sources go through the same steps:

1. `analyze_repository`: scan, then tree-sitter parsing, then name resolution.
2. Either a dry-run report or `sync_project`.

The sync step is unchanged: stamp-and-sweep, `sync_status`, failure recovery and integrity checks all work as before.

Temporary folders are created under the system temp directory with the prefix `codeatlas-`, or under `CODEATLAS_TMP_DIR` if you set it. They are deleted afterwards, on success or on error.

No source code is ever sent to the LLM during ingestion.

---

## 2. Using it in Streamlit

1. In the sidebar under **Analyze repository**, choose a **Source**:
   - **Local Directory**: enter a path, as before.
   - **GitHub URL**: enter `https://github.com/owner/repo`. You can also fill in **Branch, tag or commit**; if you leave it empty, the repository's default branch is used.
   - **ZIP Upload**: choose a `.zip` file. Then pick a **Target**:
     - **New project**, or
     - **Update an existing ZIP project** (see §4).
2. Optionally enter a **Project name**.
3. Click **Dry Run (no database writes)**. You'll see:
   - a result panel above the tabs: source, project ID, whether the project already exists, file/node/relationship counts, how imports and calls were resolved, and skipped files;
   - the raw JSON under *Diagnostics*;
   - a **Target** card in the sidebar.
4. To write the graph:
   1. Tick **"Yes, create this project…"** or **"Yes, update this existing project…"**.
   2. Click **Analyze & Write to Neo4j Aura**.

   If you click write without a dry run first, CodeAtlas shows the target card and asks you to confirm. Nothing is written or downloaded until you tick the box and click again.
5. After a write, the project list refreshes. The Graph, Statistics, Paths & impact and Ask tabs work exactly as before.

The status line names each stage: resolving, downloading or extracting, then scanning and parsing, then syncing or dry run. The analysis runs as one request, so these stages describe what happens rather than show live progress.

---

## 3. Project identity: what decides which project a run writes to

| Source | Identity (stored as `Project.source_identity`) | Project ID |
|---|---|---|
| Local | `local:<real path>` | **Unchanged formula:** `slug(name or folder) + "-" + sha1(realpath)[:8]`. The existing `sample-repo-dc690514` keeps its ID. |
| GitHub | `github:<owner>/<repo>@<ref>`. Owner and repo are lowercased and canonical as returned by GitHub's API, so renamed or differently-cased URLs give the same identity. When no ref is given, the ref is the **resolved default-branch name**. | `slug(repo) + "-" + sha1(identity)[:8]`. Your custom name changes only the display name. |
| ZIP (new project) | `zip:sha256:<fingerprint>`. The fingerprint is a SHA-256 over every extracted file's relative path and content hash. Any wrapping folder is ignored. **The file name is not part of the identity.** | `slug(name or file name without .zip) + "-" + sha1(identity)[:8]` |
| ZIP (update) | The **existing** project's identity is kept | The existing project's ID |

**How duplicates are avoided:**

- Submitting `https://github.com/o/r` and then `https://github.com/O/R.git` with ref `main` (when `main` is the default branch) resolves to the **same** identity and the same project.
- Before a GitHub or ZIP write, CodeAtlas looks up `source_identity` in Neo4j (a read). If that source is already stored under another project ID, it **targets that existing project** and says so in the target notes, rather than creating a copy.
- If the computed project ID already belongs to a *different* source, the run is refused with 409 `target_conflict`. Nothing is overwritten.

**The write guard** applies to every source, in the API and in the UI:

1. A non-dry-run request must include `expected_project_id`, equal to the target the server computed. That is the ID you were shown.
2. If that project already exists, the request must also include `confirm_update: true`.
3. Otherwise the API returns **409 `confirmation_required`** with the full target and makes no changes. For GitHub, this happens before the archive is downloaded.

Dry runs need neither field.

---

## 4. Updating a project from a new ZIP (intentional update)

A ZIP whose content has changed has a new fingerprint. Uploaded as **New project**, it becomes a separate project. To replace an existing ZIP project's graph with the new upload:

1. Choose **ZIP Upload** and select the new `.zip`.
2. Under **Target**, choose **Update an existing ZIP project** and pick the project.
3. Click **Dry Run** and check the counts. The note says files missing from this upload will be removed.
4. Tick **"Yes, update this existing project in Neo4j Aura"**, then click **Analyze & Write to Neo4j Aura**.

The usual sweep then removes files and symbols that are no longer present. Only projects created from a ZIP can be updated this way; anything else is refused with 409.

The same thing over the API:
`POST /projects/analyze/zip?target_project_id=<id>&expected_project_id=<id>&confirm_update=true`

To refresh a GitHub project, run the same URL and ref again. That gives the same identity and the same project, and you'll be asked to confirm the update. A different branch, tag or commit is a different project.

---

## 5. Security measures

### GitHub (`src/codeatlas/sources/github.py`)

- **URL checks before any request.**
  - Only `https://github.com/{owner}/{repo}` is accepted, optionally with `.git`, a trailing `/`, or `/tree/{ref}`. `www.github.com` is also accepted.
  - Rejected:
    - `http` and other hosts, including look-alikes such as `github.com.evil.io`;
    - URLs with a username or password, or a port;
    - query strings, `#` fragments, and percent-encoding;
    - gists, file/issue/blob pages, and reserved paths (`settings`, `orgs`, …);
    - control characters, and invalid owner or repo names.
- **Ref checks.** A ref must match `[A-Za-z0-9._/-]{1,200}` and must not:
  - contain `..` or `//`;
  - start with `/`, `-` or `.`;
  - end with `/`, `.` or `.lock`.
- **Two documented GitHub REST endpoints only:**
  1. `GET https://api.github.com/repos/{owner}/{repo}`. The metadata is checked: it must be a JSON object, `private` must be exactly `false`, `full_name` must be valid, and `default_branch` must pass the ref check.
  2. `GET https://api.github.com/repos/{owner}/{repo}/zipball/{ref}`. GitHub answers with a redirect to `codeload.github.com`.
- **Redirects** are followed manually, at most 3, and only to `https` on `api.github.com`, `codeload.github.com` or `github.com`, with no username or port in the URL.
- **No credentials.**
  - **Optional token:** set `GITHUB_TOKEN` (a read-only token, no scopes needed for public repos) in the backend's environment. It is never sent to the browser. It is attached only to `api.github.com` requests and removed before any archive-host request, so it never reaches a redirect target.
  - Without it, GitHub allows 60 anonymous API requests per hour per IP address; the app reports this as a rate-limit error with the ZIP alternative.
  - `trust_env=False`, so httpx ignores `.netrc` and proxy variables.
- **Timeouts:** 10 s to connect and 120 s for the download (`GITHUB_*_TIMEOUT_S`).
- **Download size:** capped at `MAX_ARCHIVE_BYTES`, checked against the declared `Content-Length` and again while streaming.
- **Readable errors:**

  | Status | Meaning |
  |---|---|
  | 400 | Invalid URL or ref |
  | 404 | Repository not found or not public, or ref not found |
  | 429 | Rate limit (unauthenticated: 60 requests per hour) |
  | 413 | Archive too large |
  | 502 | Timeout, network error, invalid metadata, or empty archive |

### ZIP (`src/codeatlas/sources/archive.py`), also used for GitHub archives

- **Upload size:** the API streams the request body and stops at `MAX_ARCHIVE_BYTES`. The UI checks the size first as well.
- **Archive format:**
  - Must be a valid ZIP.
  - Corrupt or truncated archives are rejected, and so are encrypted entries.
  - Unsupported compression methods are rejected.
- **Limits checked before anything is written:**
  - number of entries;
  - total declared uncompressed size;
  - compression ratio for entries over 1 MB (an archive-bomb check).
- **Paths:**
  - Backslashes are normalised.
  - Rejected: absolute paths, drive letters (`C:`), NUL bytes, any `..` part, duplicate entries, and a name used as both a file and a folder.
  - Every target path must resolve inside the extraction folder.
- **Symlinks and special files** are never created. They are skipped and listed as skipped. Files are opened with `O_EXCL | O_NOFOLLOW` and mode `0600`.
- **Only what the analyzer reads is written:** `.py` and `.gitignore`.
  - Each file is streamed with a hard byte limit, so a header that understates the real size cannot exceed the limits.
  - Python files over `MAX_FILE_BYTES` are skipped and reported.
- **Source root detection:**
  - `__MACOSX/`, `.DS_Store` and `Thumbs.db` are ignored.
  - While the top level holds exactly one folder and no files, CodeAtlas steps into it, up to 3 levels. This covers GitHub's `repo-<sha>/` wrapper and zipped folders.
  - Archives with no extractable `.py` files are rejected.
- **Upload file name:** reduced to its base name and `[A-Za-z0-9._ -]`, then used only for the label and the default name.

### Graph safety

- The temporary path is **never stored**. `Project.root_path` is the source label.
- New Project properties: `source_kind`, `source_identity`, `source_label`, `source_ref`.
  - They are set when a sync starts, using `coalesce`, so CLI runs don't erase them.
  - They are set again at finalize.
- Sweep, lock and failure semantics in `sync.py` are unchanged.
- Dry run never calls `sync_project` and never writes. It may read Neo4j to say whether the target exists, and still works if Neo4j is unreachable: `exists` is then "unknown". A write needs Neo4j.
- No project is ever deleted implicitly.

---

## 6. Limits (configurable in `.env`, defaults shown)

| Variable | Default | Meaning |
|---|---|---|
| `MAX_ARCHIVE_BYTES` | 200,000,000 | Maximum upload or download size |
| `MAX_EXTRACTED_BYTES` | 600,000,000 | Maximum total uncompressed size declared by the archive |
| `MAX_ARCHIVE_ENTRIES` | 100,000 | Maximum number of entries in the archive |
| `MAX_COMPRESSION_RATIO` | 200 | Maximum ratio for entries larger than `COMPRESSION_RATIO_MIN_BYTES` (1,000,000) |
| `GITHUB_TIMEOUT_S` / `GITHUB_CONNECT_TIMEOUT_S` | 120 / 10 | Download / connect timeouts |
| `CODEATLAS_TMP_DIR` | system temp | Parent folder for temporary checkouts |

The existing limits still apply: `MAX_FILES` 1,500, `MAX_FILE_BYTES` 300 KB, and the Aura capacity checks.
Streamlit's own upload limit is 200 MB by default (`server.maxUploadSize`); the CodeAtlas limit is the one that takes effect.

---

## 7. API reference

| Endpoint | Body / parameters |
|---|---|
| `POST /projects/analyze` (local, unchanged path) | JSON `{path, name?, dry_run, expected_project_id?, confirm_update?}` |
| `POST /projects/analyze/github` (new) | JSON `{url, ref?, name?, dry_run, expected_project_id?, confirm_update?}` |
| `POST /projects/analyze/zip` (new) | Raw ZIP bytes as the body (`Content-Type: application/zip`). Query parameters: `filename`, `name?`, `dry_run`, `target_project_id?`, `expected_project_id?`, `confirm_update?` |
| `GET /health` | Now also returns `max_upload_bytes` |
| `GET /projects` | Now also returns `source_kind` and `source_label` |

Every analyze response is an `AnalysisReport`. It has the same fields as before, plus `source`, the target information:

- `kind`, `label`, `identity`, `project_id`, `project_name`;
- `ref`, `ref_is_default`, `fingerprint`;
- `exists`, `existing_*`;
- `notes`.

Examples. Dry runs never write. The GitHub one contacts GitHub, so run it only when you intend to.

```bash
curl -s -X POST localhost:8000/projects/analyze/github -H 'content-type: application/json' \
     -d '{"url":"https://github.com/owner/repo","dry_run":true}'
curl -s -X POST 'localhost:8000/projects/analyze/zip?filename=repo.zip&dry_run=true' \
     -H 'content-type: application/zip' --data-binary @repo.zip
```

The CLI (`python -m codeatlas.cli analyze PATH`) is unchanged. It handles local paths only and has no confirmation step.

---

## 8. Files changed

**New**

| File | Purpose |
|---|---|
| `src/codeatlas/sources/__init__.py` | Package docstring |
| `src/codeatlas/sources/errors.py` | Source error types, each with an HTTP status |
| `src/codeatlas/sources/github.py` | URL and ref checks, metadata lookup, restricted redirects, capped download |
| `src/codeatlas/sources/archive.py` | Safe ZIP extraction, source root detection, fingerprint, `temp_workspace` |
| `src/codeatlas/sources/service.py` | Shared workflow: work out the target, check whether it exists (read-only), write guard, analyze, then dry run or sync |
| `tests/test_sources_archive.py` | 25 tests |
| `tests/test_sources_github.py` | 85 tests |
| `tests/test_analyze_routing.py` | 24 tests, including 2 in-process Streamlit `AppTest` tests |
| `docs/REPOSITORY_SOURCES.md` | This document |

**Modified (small and additive)**

| File | Change |
|---|---|
| `src/codeatlas/config.py` | New archive and GitHub limits. No credential settings. |
| `src/codeatlas/ids.py` | `slugify()` and `project_id_for_identity()`. `project_id_for()` gives exactly the same output as before. |
| `src/codeatlas/models.py` | `SourceTarget`, `WriteGuard`, `GithubAnalyzeRequest`. `AnalyzeRequest` gains the guard fields; `AnalysisReport.source` added. |
| `src/codeatlas/analysis/pipeline.py` | Optional `source=` argument. Without it, behaviour is identical. Adds the `AnalysisOutput.root_label` property. |
| `src/codeatlas/graph/sync.py` | `root_path` now comes from `root_label`. The begin query also sets the `source_*` fields with `coalesce`. The report carries `source`. |
| `src/codeatlas/graph/queries.py` | `LIST_PROJECTS` returns `source_kind` and `source_label`. New read query `PROJECTS_BY_IDENTITY`. |
| `src/codeatlas/cli.py` | `catalog()` includes the new query, so the static Cypher checks cover it. |
| `src/codeatlas/api/main.py` | Shared `_run_analysis` error mapping, the `/github` and `/zip` endpoints, a `SourceError` handler (409 detail carries the target), and `max_upload_bytes` in `/health`. |
| `ui/app.py` | Source selector, three input forms, target card and confirmation, separate Dry Run and Write buttons, readable result panel with *Diagnostics*. `api()` now sits on top of a new `api_raw()`. Project selection, graph, statistics, paths, impact and Ask are untouched. |
| `tests/conftest.py` | `build_zip()` and `fixture_zip()` helpers |
| `docs/RUN_AND_VERIFY.md` | Streamlit checklist row and troubleshooting rows |

**Not touched:**

- `Understand-Anything/`, `.env`, `.gitignore`, `data/`, `.git`;
- `pyproject.toml`, `uv.lock`, and installed packages;
- Aura.

---

## 9. Tests (offline: no GitHub, no Aura writes, no LLM)

Run them from `CodeAtlas/` with `source ../.venv/bin/activate && python -m pytest`.

Result: **296 passed, 4 deselected**. The 4 are the opt-in `-m neo4j` integration tests, which were not run. Before this change there were 160, and the static catalog test added 2.

What is covered:

- **URL validation:** 11 valid forms and 31 invalid ones, plus 14 invalid refs and conflicting refs.
- **Mocked GitHub (`httpx.MockTransport`):**
  - default branch resolution, and the default branch matching its explicit name;
  - refs containing a slash;
  - 404, 451 and 403 responses, the rate limit (403 with headers, and 429), and 500;
  - invalid, private, incomplete and oversized metadata;
  - disallowed redirects (5 variants) and redirect loops;
  - a missing ref, timeouts and connection errors;
  - the size cap (declared and streamed) and an empty archive;
  - no credentials sent, even with `GITHUB_TOKEN` set.
- **ZIP:**
  - flat root, wrapper folder, nested wrappers, `__MACOSX` ignored;
  - fingerprint stability;
  - six path-traversal and absolute-path variants, and NUL bytes;
  - symlinks, encrypted entries, not a ZIP, truncated archives, no Python files;
  - oversized files skipped;
  - entry count, archive size and total size limits, the ratio bomb, an understated size header;
  - duplicate entries and file/folder conflicts;
  - temp folder cleanup on success and on error.
- **Routing through the real FastAPI app** with an in-memory project store, a stubbed `sync_project`, and **any real Neo4j call failing the test**:
  - the local project ID formula is unchanged;
  - GitHub and ZIP produce **the same node and relationship counts as the local run of the same fixture**;
  - dry runs never sync;
  - the write guard: target confirmation, confirming updates, a wrong expected ID, the guard running before any download;
  - Neo4j unreachable (dry run works, write returns 503);
  - no duplicates for default branch vs explicit name;
  - ZIP identity is the content, not the file name;
  - explicit ZIP update, update refused for non-ZIP projects, project ID collisions;
  - bad, empty, oversized and unsafe uploads;
  - file name sanitising;
  - temp cleanup on success, failure and analyzer crashes.
- **Streamlit (`AppTest`, in-process, no server):**
  - local Dry Run, then a write refused until the box is ticked, then a confirmed write;
  - a GitHub write shows the target label before writing.

---

## 10. Assumptions and limitations

- **Not yet run against the live services.**
  - The GitHub flow uses GitHub's documented REST endpoints, but it was only tested against mocks. No real GitHub request has been made.
  - The changed begin-sync query and the new `PROJECTS_BY_IDENTITY` query passed the offline static Cypher checks, but haven't run on Aura yet.
- **Restart needed.** The running API server needs a restart to load the new endpoints, and Streamlit needs a refresh or restart.
- **Rate limit.** Unauthenticated GitHub API calls are limited to 60 per hour per IP, and each GitHub run uses 2. Private repositories are not supported, by design.
- **Refs.** `refs/heads/main` and `main` are different refs, and therefore different identities. A commit SHA is its own project.
- **Race window.** There is a small gap between the confirmation check and the write. The sync's own project lock still stops two writes running at once.
- **Progress** is shown by stage, not live, because each analysis is a single request.
- **Neo4j warnings.** Projects synced before this change have no `source_*` properties until they are synced again. Until then, Neo4j may log a harmless "property does not exist" notice for `/projects`, like the existing `sync_error` one. The first local re-sync from the UI will ask for update confirmation, because the project exists.
- **ZIP updates follow the archive.** Files that are not in the new archive are removed from that project's graph by the normal sweep.

## 11. Next steps that need your separate approval

1. Restart uvicorn and Streamlit to load the new code.
2. Run a live dry run against a real public GitHub repository. This makes an outbound request to GitHub.
3. Do a real write from any source, which writes to Aura.
4. Run `pytest -m neo4j`, which writes `test-*` projects to Aura and deletes them afterwards.
