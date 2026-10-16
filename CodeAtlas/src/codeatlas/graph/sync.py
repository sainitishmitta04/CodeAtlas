"""Project-scoped, retryable synchronization of analysis rows into Neo4j.

Algorithm (one run = one fresh run_id):

1. begin   : MERGE the Project node, take a soft lock: sync_status='writing',
             pending_run_id=$run_id. A concurrent non-stale run is rejected.
2. write   : batched `UNWIND ... MERGE ... SET += props, last_run_id=$run_id` for all
             nodes, then all relationships. Every relationship batch verifies that both
             endpoints matched (row count == written count).
3. sweep   : ONLY after every batch succeeded: sync_status='sweeping'; delete this
             project's relationships, then nodes, whose last_run_id <> $run_id
             (chunked, label-anchored, filtered by project_id).
4. finalize: sync_status='complete', last_run_id=$run_id, stats.

Failure semantics (what the graph looks like if a run dies):
- During write: batches already committed have overwritten properties / added nodes and
  edges; nothing has been deleted. The project graph is a *mixture* of the previous run
  (untouched or stale items) and the failed run (written items). It is NOT the previous
  graph. Project.sync_status='failed' (best effort) marks it as not trustworthy; the UI
  and Q&A surface that warning.
- During sweep: some stale items are already gone; the rest remain. Same flag.
- Recovery: simply re-run analysis. A new run_id rewrites every current item and the
  sweep removes everything not stamped with it, so the project converges to exactly the
  current source state regardless of where the previous run stopped. Other projects are
  never touched because every MATCH is anchored on this project's id/project_id.
"""

import logging
import time
import uuid
from datetime import datetime, timezone

from codeatlas.analysis.pipeline import AnalysisOutput
from codeatlas.config import Settings
from codeatlas.graph import db, schema
from codeatlas.models import AnalysisReport, GraphRows

log = logging.getLogger(__name__)

# variant key -> (source label, relationship type, target label)
REL_VARIANTS: dict[str, tuple[str, str, str]] = {
    "CONTAINS": ("Project", "CONTAINS", "File"),
    "DEFINES_CLASS": ("File", "DEFINES", "Class"),
    "DEFINES_FUNCTION": ("File", "DEFINES", "Function"),
    "HAS_METHOD": ("Class", "HAS_METHOD", "Function"),
    "IMPORTS_FILE": ("File", "IMPORTS", "File"),
    "IMPORTS_MODULE": ("File", "IMPORTS", "Module"),
    "INHERITS": ("Class", "INHERITS", "Class"),
    "CALLS": ("Function", "CALLS", "Function"),
}
SWEPT_LABELS = ("Function", "Class", "Module", "File")  # never the Project node
STALE_LOCK_MS = 15 * 60 * 1000


class SyncConflict(RuntimeError):
    pass


class CapacityError(RuntimeError):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _anchor(label: str) -> str:
    """Index-backed, project-scoped anchor for a label."""
    return "{id: $pid}" if label == "Project" else "{project_id: $pid}"


# ------------------------------------------------------------------ query builders
# Labels/types come only from the fixed tables above, never from user input.

def node_merge_query(label: str) -> str:
    return (f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) "
            "SET n += row.props, n.project_id = $pid, n.last_run_id = $run_id "
            "RETURN count(n) AS written")


def rel_merge_query(variant: str) -> str:
    src, rel, dst = REL_VARIANTS[variant]
    return (f"UNWIND $rows AS row "
            f"MATCH (a:{src} {{id: row.source}}) MATCH (b:{dst} {{id: row.target}}) "
            f"MERGE (a)-[r:{rel}]->(b) SET r += row.props, r.last_run_id = $run_id "
            "RETURN count(r) AS written")


def rel_sweep_query(variant: str) -> str:
    src, rel, dst = REL_VARIANTS[variant]
    return (f"MATCH (a:{src} {_anchor(src)})-[r:{rel}]->(:{dst}) "
            "WHERE r.last_run_id IS NULL OR r.last_run_id <> $run_id "
            "WITH r LIMIT $chunk DELETE r RETURN count(*) AS deleted")


def node_sweep_query(label: str) -> str:
    return (f"MATCH (n:{label} {{project_id: $pid}}) "
            "WHERE n.last_run_id IS NULL OR n.last_run_id <> $run_id "
            "WITH n LIMIT $chunk DETACH DELETE n RETURN count(*) AS deleted")


BEGIN_QUERY = """
MERGE (p:Project {id: $pid})
ON CREATE SET p.created_at = $now_iso
SET p.sync_lock_touch = $now_ms
WITH p, coalesce(p.sync_status IN ['writing', 'sweeping']
                 AND p.sync_heartbeat >= $stale_before, false) AS busy
FOREACH (_ IN CASE WHEN busy AND NOT $force THEN [] ELSE [1] END |
  SET p.project_id = $pid, p.source = 'codeatlas', p.name = $name, p.root_path = $root,
      p.sync_status = 'writing', p.pending_run_id = $run_id,
      p.sync_started_at = $now_iso, p.sync_heartbeat = $now_ms,
      p.sync_error = null, p.sync_failed_phase = null)
RETURN busy, p.pending_run_id = $run_id AS acquired
"""

PHASE_QUERY = """
MATCH (p:Project {id: $pid}) WHERE p.pending_run_id = $run_id
SET p.sync_status = $status, p.sync_heartbeat = $now_ms
RETURN count(p) AS ok
"""

FINALIZE_QUERY = """
MATCH (p:Project {id: $pid}) WHERE p.pending_run_id = $run_id
SET p += $props, p.sync_status = 'complete', p.last_run_id = $run_id,
    p.pending_run_id = null, p.synced_at = $now_iso, p.sync_error = null,
    p.sync_failed_phase = null
RETURN count(p) AS ok
"""

FAIL_QUERY = """
MATCH (p:Project {id: $pid}) WHERE p.pending_run_id = $run_id
SET p.sync_status = 'failed', p.sync_error = $error, p.sync_failed_phase = $phase
"""

NODE_TOTAL_QUERY = "MATCH (n) RETURN count(n) AS n"  # answered from the count store
REL_TOTAL_QUERY = "MATCH ()-[r]->() RETURN count(r) AS n"


def _batches(rows: list, size: int):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def check_capacity(rows: GraphRows, settings: Settings) -> None:
    """Refuse oversized projects before writing anything (AuraDB Free limits)."""
    planned_nodes, planned_rels = rows.node_count(), rows.rel_count()
    if planned_nodes > settings.max_project_nodes or planned_rels > settings.max_project_rels:
        raise CapacityError(
            f"Project too large for this MVP: {planned_nodes} nodes / {planned_rels} relationships "
            f"(limits {settings.max_project_nodes} / {settings.max_project_rels}). "
            "Analyze a subdirectory.")
    total_nodes = db.read(NODE_TOTAL_QUERY)[0]["n"]
    total_rels = db.read(REL_TOTAL_QUERY)[0]["n"]
    # worst case during a sync: stale and new items coexist until the sweep runs
    if total_nodes + planned_nodes > settings.aura_node_limit or \
            total_rels + planned_rels > settings.aura_rel_limit:
        raise CapacityError(
            f"Database would exceed configured limits ({settings.aura_node_limit} nodes / "
            f"{settings.aura_rel_limit} relationships) during sync.")


def sync_project(out: AnalysisOutput, settings: Settings, force: bool = False) -> AnalysisReport:
    started = time.time()
    rows = out.rows
    pid, run_id = out.project_id, uuid.uuid4().hex
    report = _base_report(out, dry_run=False)
    report.run_id = run_id

    schema.ensure_schema()
    check_capacity(rows, settings)

    now_iso = datetime.now(timezone.utc).isoformat()
    lock = db.write(BEGIN_QUERY, {
        "pid": pid, "run_id": run_id, "name": out.project_name, "root": out.scan.root,
        "now_iso": now_iso, "now_ms": _now_ms(), "stale_before": _now_ms() - STALE_LOCK_MS,
        "force": force,
    })[0]
    if not lock["acquired"]:
        raise SyncConflict(f"Another sync of project {pid} is in progress; retry later "
                           "or force after it goes stale.")

    phase = "write"
    base = {"pid": pid, "run_id": run_id}
    try:
        for label, node_rows in rows.nodes.items():
            for batch in _batches(node_rows, settings.write_batch_size):
                db.write(node_merge_query(label), {**base, "rows": [r.model_dump() for r in batch]})
            db.write(PHASE_QUERY, {**base, "status": "writing", "now_ms": _now_ms()})
        for variant, rel_rows in rows.rels.items():
            for batch in _batches(rel_rows, settings.write_batch_size):
                written = db.write(rel_merge_query(variant),
                                   {**base, "rows": [r.model_dump() for r in batch]})[0]["written"]
                if written != len(batch):
                    raise RuntimeError(f"{variant}: {len(batch) - written} relationships had "
                                       "missing endpoints")
            db.write(PHASE_QUERY, {**base, "status": "writing", "now_ms": _now_ms()})

        phase = "sweep"
        db.write(PHASE_QUERY, {**base, "status": "sweeping", "now_ms": _now_ms()})
        for variant in REL_VARIANTS:
            report.swept_rels += _sweep(rel_sweep_query(variant), base, settings)
        for label in SWEPT_LABELS:
            report.swept_nodes += _sweep(node_sweep_query(label), base, settings)

        phase = "finalize"
        props = {**rows.project.props, **{f"count_{k.lower()}": v for k, v in report.node_counts.items() if k != "Project"},
                 **{f"resolution_{k}": v for k, v in out.stats.model_dump().items()}}
        ok = db.write(FINALIZE_QUERY, {**base, "props": props,
                                       "now_iso": datetime.now(timezone.utc).isoformat()})
        if not ok or ok[0]["ok"] != 1:
            raise SyncConflict("Sync lock was taken over by another run; this run's sweep "
                               "completed but finalize was skipped. Re-run analysis.")
    except Exception as exc:
        try:
            db.write(FAIL_QUERY, {**base, "error": str(exc)[:500], "phase": phase})
        except Exception:  # noqa: BLE001 - database may be the thing that failed
            log.warning("could not record sync failure for %s", pid)
        raise
    report.sync_status = "complete"
    report.duration_s = round(time.time() - started, 2)
    return report


def _sweep(query: str, base: dict, settings: Settings) -> int:
    total = 0
    while True:
        deleted = db.write(query, {**base, "chunk": settings.sweep_chunk_size})[0]["deleted"]
        total += deleted
        if deleted == 0:
            return total


def _base_report(out: AnalysisOutput, dry_run: bool) -> AnalysisReport:
    rels: dict[str, int] = {}
    for variant, rel_rows in out.rows.rels.items():
        rel_type = REL_VARIANTS[variant][1]
        rels[rel_type] = rels.get(rel_type, 0) + len(rel_rows)
    return AnalysisReport(
        project_id=out.project_id, project_name=out.project_name, root_path=out.scan.root,
        dry_run=dry_run, files_analyzed=len(out.analyses),
        files_with_syntax_errors=[a.path for a in out.analyses if a.has_syntax_errors],
        skipped=out.scan.skipped,
        node_counts={"Project": 1, **{k: len(v) for k, v in out.rows.nodes.items()}},
        rel_counts=rels, resolution=out.stats,
    )


def dry_run_report(out: AnalysisOutput) -> AnalysisReport:
    return _base_report(out, dry_run=True)


DELETE_LABEL_QUERY = """
MATCH (n:{label} {{project_id: $pid}}) WITH n LIMIT $chunk DETACH DELETE n RETURN count(*) AS deleted
"""


def delete_project(pid: str, settings: Settings) -> int:
    """Remove one project's subgraph only (used by tests and explicit cleanup)."""
    total = 0
    for label in SWEPT_LABELS:
        q = DELETE_LABEL_QUERY.format(label=label)
        while True:
            n = db.write(q, {"pid": pid, "chunk": settings.sweep_chunk_size})[0]["deleted"]
            total += n
            if n == 0:
                break
    db.write("MATCH (p:Project {id: $pid}) DETACH DELETE p", {"pid": pid})
    return total + 1
