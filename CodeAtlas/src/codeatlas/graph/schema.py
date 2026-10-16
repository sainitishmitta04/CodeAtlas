"""Idempotent constraints and indexes. Running this is a schema write (needs approval)."""

from codeatlas.graph import db

NODE_LABELS = ("Project", "File", "Module", "Class", "Function")

SCHEMA_STATEMENTS = [
    *(f"CREATE CONSTRAINT codeatlas_{label.lower()}_id IF NOT EXISTS "
      f"FOR (n:{label}) REQUIRE n.id IS UNIQUE" for label in NODE_LABELS),
    # project-scoped lookups/sweeps; Project nodes also carry project_id (= id)
    *(f"CREATE INDEX codeatlas_{label.lower()}_project IF NOT EXISTS "
      f"FOR (n:{label}) ON (n.project_id)" for label in NODE_LABELS if label != "Project"),
    "CREATE INDEX codeatlas_file_path IF NOT EXISTS FOR (n:File) ON (n.project_id, n.path)",
    "CREATE INDEX codeatlas_function_name IF NOT EXISTS FOR (n:Function) ON (n.project_id, n.name)",
    "CREATE INDEX codeatlas_class_name IF NOT EXISTS FOR (n:Class) ON (n.project_id, n.name)",
]


def ensure_schema() -> list[str]:
    for stmt in SCHEMA_STATEMENTS:
        db.write(stmt)
    return SCHEMA_STATEMENTS


def schema_present() -> bool:
    rows = db.read("SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH 'codeatlas_' "
                   "RETURN count(*) AS n")
    return bool(rows) and rows[0]["n"] >= len(NODE_LABELS)
