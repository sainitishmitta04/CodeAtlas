"""FastAPI backend. Run: uvicorn codeatlas.api.main:app --reload (from CodeAtlas/)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from codeatlas.analysis.scanner import ScanError
from codeatlas.config import ROOT_ENV_FILE, get_settings, shadowed_env_names
from codeatlas.graph import db, queries
from codeatlas.graph.db import GraphDBError
from codeatlas.graph.sync import CapacityError, SyncConflict
from codeatlas.models import (AnalysisReport, AnalyzeRequest, Answer, AskRequest,
                              GithubAnalyzeRequest, GraphPayload)
from codeatlas.llm import client as llm_client
from codeatlas.qa.answer import AnswerFailed, answer_question
from codeatlas.sources import service
from codeatlas.sources.archive import temp_workspace
from codeatlas.sources.errors import SourceError, SourceTooLarge

logging.basicConfig(level=get_settings().log_level.upper())


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    db.close_driver()


app = FastAPI(title="CodeAtlas API", version="0.1.0", lifespan=lifespan)


# Every error body carries `component` (neo4j | llm | github | source | api) so clients can
# tell which dependency failed. Messages are user-safe; no credentials are ever included.

@app.exception_handler(GraphDBError)
async def _db_error(_, exc: GraphDBError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc), "component": "neo4j"})


@app.exception_handler(queries.QueryInputError)
async def _input_error(_, exc: queries.QueryInputError):
    return JSONResponse(status_code=400, content={"detail": str(exc), "component": "api"})


@app.exception_handler(AnswerFailed)
async def _answer_failed(_, exc: AnswerFailed):
    """The graph retrieval worked but the LLM did not answer: an honest non-2xx response that
    still carries the retrieved graph facts, explicitly marked as not being an answer."""
    detail = {**exc.error.to_dict(), "code": f"llm_{exc.error.code}", "component": "llm",
              "graph_context": exc.partial.model_dump(mode="json")}
    return JSONResponse(status_code=exc.error.http_status,
                        content={"detail": detail, "component": "llm"})


@app.exception_handler(SourceError)
async def _source_error(_, exc: SourceError):
    if isinstance(exc, service.ConfirmationRequired):
        detail = {"code": exc.code, "message": exc.message,
                  "target": exc.target.model_dump(mode="json")}
    else:
        detail = exc.message
    return JSONResponse(status_code=exc.status,
                        content={"detail": detail, "component": exc.component})


@app.get("/health")
def health() -> dict:
    """Lightweight: pings Neo4j, but never calls the LLM provider. `llm.last_call` is the
    outcome of the most recent real /ask ('unknown' until one is made), so a configured key
    is not presented as a working one."""
    s = get_settings()
    return {"neo4j": db.ping() if s.neo4j_configured else {"ok": False, "error": "not configured"},
            "llm_configured": s.llm_configured, "model": s.gemini_model,
            "llm": {**llm_client.config_summary(s), "last_call": llm_client.status_snapshot()},
            "config": {"env_file_found": ROOT_ENV_FILE is not None,
                       "shell_vars_ignored_in_favour_of_env_file": shadowed_env_names()},
            "max_upload_bytes": s.max_archive_bytes}


def _run_analysis(fn, *args, **kw) -> AnalysisReport:
    """Shared error mapping for every source (local, GitHub, ZIP)."""
    try:
        return fn(*args, **kw)
    except ScanError as exc:
        raise HTTPException(400, str(exc)) from exc
    except CapacityError as exc:
        raise HTTPException(413, str(exc)) from exc
    except SyncConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/analyze", response_model=AnalysisReport)
def analyze(req: AnalyzeRequest) -> AnalysisReport:
    """Local directory."""
    return _run_analysis(service.analyze_local, req, get_settings())


@app.post("/projects/analyze/github", response_model=AnalysisReport)
def analyze_github(req: GithubAnalyzeRequest) -> AnalysisReport:
    """Public GitHub repository (archive download; no git, no token)."""
    return _run_analysis(service.analyze_github, req, get_settings())


@app.post("/projects/analyze/zip", response_model=AnalysisReport)
async def analyze_zip(request: Request,
                      filename: str = Query("upload.zip", max_length=255),
                      name: str | None = Query(None, max_length=200),
                      dry_run: bool = False,
                      target_project_id: str | None = Query(None, max_length=300),
                      expected_project_id: str | None = Query(None, max_length=300),
                      confirm_update: bool = False) -> AnalysisReport:
    """ZIP upload sent as the raw request body (Content-Type: application/zip)."""
    settings = get_settings()
    limit = settings.max_archive_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise SourceTooLarge(f"Upload is larger than {limit:,} bytes.")
    with temp_workspace(settings) as work:
        upload, total = work / "upload.zip", 0
        with open(upload, "wb") as fh:
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    raise SourceTooLarge(f"Upload is larger than {limit:,} bytes.")
                fh.write(chunk)
        if total == 0:
            raise HTTPException(400, "Empty upload; send the ZIP file as the request body.")
        return await run_in_threadpool(
            _run_analysis, service.analyze_zip_file, upload, work, settings,
            filename=filename, name=name or None, target_project_id=target_project_id or None,
            dry_run=dry_run, expected_project_id=expected_project_id,
            confirm_update=confirm_update)


@app.get("/projects")
def projects() -> list[dict]:
    return queries.list_projects()


@app.get("/projects/{pid}")
def project(pid: str) -> dict:
    p = queries.project(pid)
    if p is None:
        raise HTTPException(404, "Unknown project")
    return p


@app.get("/projects/{pid}/stats")
def stats(pid: str) -> dict:
    return queries.stats(pid)


@app.get("/projects/{pid}/integrity")
def integrity(pid: str) -> dict:
    return queries.integrity(pid)


@app.get("/projects/{pid}/files")
def files(pid: str) -> list[dict]:
    return queries.list_files(pid)


@app.get("/projects/{pid}/functions")
def functions(pid: str) -> list[dict]:
    return queries.list_functions(pid)


@app.get("/graph/overview", response_model=GraphPayload)
def graph_overview(project_id: str, labels: list[str] = Query(default=list(queries.LABELS)),
                   rel_types: list[str] = Query(default=list(queries.REL_TYPES)),
                   limit: int = queries.OVERVIEW_PAGE_SIZE, after: str = "",
                   include_totals: bool | None = None) -> GraphPayload:
    """One page of the project's graph (`limit` = page size). Repeat with
    `after=<next_cursor>` until `complete` is true to obtain the complete graph."""
    return queries.overview(project_id, labels, rel_types, limit, after, include_totals)


@app.get("/graph/neighborhood", response_model=GraphPayload)
def graph_neighborhood(label: str, node_id: str, depth: int = 1,
                       rel_types: list[str] = Query(default=list(queries.REL_TYPES)),
                       limit: int = 100) -> GraphPayload:
    return queries.neighborhood(label, node_id, depth, rel_types, limit)


@app.get("/graph/path", response_model=GraphPayload)
def graph_path(mode: str, from_id: str, to_id: str, from_label: str = "File",
               to_label: str = "File", max_hops: int = 6) -> GraphPayload:
    return queries.path_between(mode, from_id, to_id, from_label, to_label, max_hops)


@app.get("/graph/impact")
def graph_impact(label: str, node_id: str, depth: int = 2) -> dict:
    return queries.impact(label, node_id, depth)


@app.post("/ask", response_model=Answer)
def ask(req: AskRequest) -> Answer:
    return answer_question(req.project_id, req.question, req.focus_label, req.focus_id)
