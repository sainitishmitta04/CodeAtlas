"""FastAPI backend. Run: uvicorn codeatlas.api.main:app --reload (from CodeAtlas/)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.analysis.scanner import ScanError
from codeatlas.config import get_settings
from codeatlas.graph import db, queries
from codeatlas.graph.db import GraphDBError
from codeatlas.graph.sync import CapacityError, SyncConflict, dry_run_report, sync_project
from codeatlas.models import AnalysisReport, AnalyzeRequest, Answer, AskRequest, GraphPayload
from codeatlas.qa.answer import answer_question

logging.basicConfig(level=get_settings().log_level.upper())


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    db.close_driver()


app = FastAPI(title="CodeAtlas API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(GraphDBError)
async def _db_error(_, exc: GraphDBError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(queries.QueryInputError)
async def _input_error(_, exc: queries.QueryInputError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"neo4j": db.ping() if s.neo4j_configured else {"ok": False, "error": "not configured"},
            "llm_configured": s.llm_configured, "model": s.openai_model}


@app.post("/projects/analyze", response_model=AnalysisReport)
def analyze(req: AnalyzeRequest) -> AnalysisReport:
    settings = get_settings()
    try:
        out = analyze_repository(req.path, settings, req.name)
    except ScanError as exc:
        raise HTTPException(400, str(exc)) from exc
    if req.dry_run:
        return dry_run_report(out)
    try:
        return sync_project(out, settings)
    except CapacityError as exc:
        raise HTTPException(413, str(exc)) from exc
    except SyncConflict as exc:
        raise HTTPException(409, str(exc)) from exc


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
                   limit: int = 150) -> GraphPayload:
    return queries.overview(project_id, labels, rel_types, limit)


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
