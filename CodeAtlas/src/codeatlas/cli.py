"""Command line helpers.

  python -m codeatlas.cli analyze PATH [--name N] [--dry-run]   # dry-run: no database access
  python -m codeatlas.cli validate-cypher                       # EXPLAIN only, read session
  python -m codeatlas.cli integrity PROJECT_ID
  python -m codeatlas.cli delete-project PROJECT_ID --yes       # removes ONLY that project
"""

import argparse
import json
import sys

from neo4j import READ_ACCESS

from codeatlas.analysis.pipeline import analyze_repository
from codeatlas.config import get_settings
from codeatlas.graph import db, queries, sync
from codeatlas.graph.schema import SCHEMA_STATEMENTS


def catalog() -> dict[str, str]:
    """Every Cypher statement the app can issue, with representative label/depth choices."""
    q: dict[str, str] = {
        "list_projects": queries.LIST_PROJECTS, "project": queries.PROJECT,
        "node_stats": queries.NODE_STATS, "rel_stats": queries.REL_STATS,
        "call_confidence": queries.CALL_CONFIDENCE, "import_resolution": queries.IMPORT_RESOLUTION,
        "top_imported_files": queries.TOP_IMPORTED_FILES,
        "top_called_functions": queries.TOP_CALLED_FUNCTIONS,
        "list_files": queries.LIST_FILES, "list_functions": queries.LIST_FUNCTIONS,
        "overview_all": queries.overview_query(list(queries.LABELS)),
        "overview_files": queries.overview_query(["File"]),
        "find_entities": queries.FIND_ENTITIES,
        "impact_defined": queries.IMPACT_DEFINED,
        "impact_importers": queries.IMPACT_IMPORTERS % 3,
        "impact_callers_of_file": queries.IMPACT_CALLERS_OF_FILE % 3,
        "impact_subclasses_of_file": queries.IMPACT_SUBCLASSES_OF_FILE % 3,
        "impact_callers_of_function": queries.IMPACT_CALLERS_OF_FUNCTION % 3,
        "impact_callers_of_class": queries.IMPACT_CALLERS_OF_CLASS % 3,
        "impact_subclasses_of_class": queries.IMPACT_SUBCLASSES_OF_CLASS % 3,
        "impact_importers_of_module": queries.IMPACT_IMPORTERS_OF_MODULE,
        "path_dependency": queries.PATH_QUERIES["dependency"] % 6,
        "path_call": queries.PATH_QUERIES["call"] % 6,
        "path_connection": queries.PATH_QUERIES["connection"] % ("Class", "Function", 6),
        "sync_begin": sync.BEGIN_QUERY, "sync_phase": sync.PHASE_QUERY,
        "sync_finalize": sync.FINALIZE_QUERY, "sync_fail": sync.FAIL_QUERY,
        "node_total": sync.NODE_TOTAL_QUERY, "rel_total": sync.REL_TOTAL_QUERY,
    }
    for label in queries.LABELS:
        q[f"neighborhood_{label}"] = queries.neighborhood_query(label, 2)
    for name, check in queries.INTEGRITY_CHECKS.items():
        q[f"integrity_{name}"] = check
    for label in ("File", "Module", "Class", "Function"):
        q[f"merge_{label}"] = sync.node_merge_query(label)
        q[f"sweep_{label}"] = sync.node_sweep_query(label)
    for variant in sync.REL_VARIANTS:
        q[f"merge_{variant}"] = sync.rel_merge_query(variant)
        q[f"sweep_{variant}"] = sync.rel_sweep_query(variant)
    return q


def _notifications(summary) -> list[str]:
    out = []
    for n in getattr(summary, "summary_notifications", None) or []:
        out.append(f"{n.severity_level}: {n.title} {n.description}"[:300])
    return out


def validate_cypher() -> int:
    """EXPLAIN every statement (plans only; nothing is executed or written)."""
    s = get_settings()
    driver = db.get_driver()
    info = driver.get_server_info()
    print(f"server: {info.agent} protocol {info.protocol_version}")
    failures = 0
    with driver.session(database=s.neo4j_database, default_access_mode=READ_ACCESS) as session:
        for name, text in catalog().items():
            try:
                summary = session.run("EXPLAIN " + text.strip()).consume()
                notes = [n for n in _notifications(summary)
                         if "label does not exist" not in n.lower()
                         and "relationship type does not exist" not in n.lower()
                         and "property key does not exist" not in n.lower()
                         and "unknown" not in n.lower()]
                print(f"OK    {name}" + (f"  [{'; '.join(notes)}]" if notes else ""))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL  {name}: {getattr(exc, 'code', '')} {getattr(exc, 'message', exc)}")
    for stmt in SCHEMA_STATEMENTS:
        print(f"(schema, not executed) {stmt}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="codeatlas")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("path")
    a.add_argument("--name")
    a.add_argument("--dry-run", action="store_true")
    sub.add_parser("validate-cypher")
    i = sub.add_parser("integrity")
    i.add_argument("project_id")
    d = sub.add_parser("delete-project")
    d.add_argument("project_id")
    d.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)
    settings = get_settings()

    try:
        if args.cmd == "analyze":
            out = analyze_repository(args.path, settings, args.name)
            report = sync.dry_run_report(out) if args.dry_run else sync.sync_project(out, settings)
            print(report.model_dump_json(indent=2))
        elif args.cmd == "validate-cypher":
            return validate_cypher()
        elif args.cmd == "integrity":
            print(json.dumps(queries.integrity(args.project_id), indent=2))
        elif args.cmd == "delete-project":
            if not args.yes:
                print("Refusing without --yes")
                return 2
            print(f"deleted {sync.delete_project(args.project_id, settings)} nodes")
    finally:
        db.close_driver()
    return 0


if __name__ == "__main__":
    sys.exit(main())
