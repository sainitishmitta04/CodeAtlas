"""Neo4j driver lifecycle and small read/write helpers with timeouts and error mapping."""

import logging
from typing import Any

from neo4j import Driver, GraphDatabase, unit_of_work
from neo4j.exceptions import (
    AuthError, ClientError, ConfigurationError, Neo4jError, ServiceUnavailable, SessionExpired,
    TransientError,
)

from codeatlas.config import Settings, get_settings

log = logging.getLogger(__name__)


class GraphDBError(RuntimeError):
    """A database problem expressed in user-safe terms. `status` maps to an HTTP status."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


_driver: Driver | None = None


def get_driver(settings: Settings | None = None) -> Driver:
    global _driver
    settings = settings or get_settings()
    if _driver is None:
        if not settings.neo4j_configured:
            raise GraphDBError("Neo4j is not configured (NEO4J_URI / NEO4J_PASSWORD missing).")
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password.get_secret_value()),
            max_connection_pool_size=10,
            connection_timeout=15,
            max_transaction_retry_time=15,
        )
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def _map_error(exc: Exception) -> GraphDBError:
    if isinstance(exc, AuthError):
        return GraphDBError("Neo4j authentication failed; check NEO4J_USERNAME/NEO4J_PASSWORD.")
    if isinstance(exc, (ServiceUnavailable, SessionExpired)):
        return GraphDBError("Neo4j is unreachable. If this is AuraDB Free, the instance may be "
                            "paused; resume it in the Aura console.")
    if isinstance(exc, TransientError):
        return GraphDBError(f"Neo4j transient error (retry later): {exc.code}")
    if isinstance(exc, ClientError):
        if exc.code and "TransactionTimedOut" in exc.code:
            return GraphDBError("Query timed out; reduce depth or limits.", status=504)
        return GraphDBError(f"Neo4j rejected the query: {exc.code}: {exc.message}", status=400)
    if isinstance(exc, (Neo4jError, ConfigurationError)):
        return GraphDBError(f"Neo4j error: {exc}")
    return GraphDBError(f"Unexpected database error: {exc}")


def _run(query: str, params: dict[str, Any], write: bool, timeout: float) -> list[dict]:
    settings = get_settings()

    @unit_of_work(timeout=timeout)
    def work(tx):
        # keep raw driver values (Node/Relationship/Path) for graph payload building
        return [dict(r.items()) for r in tx.run(query, params)]

    try:
        with get_driver().session(database=settings.neo4j_database) as session:
            return session.execute_write(work) if write else session.execute_read(work)
    except GraphDBError:
        raise
    except Exception as exc:  # noqa: BLE001 - mapped to a user-safe error
        log.warning("Neo4j %s failed: %s", "write" if write else "read", exc)
        raise _map_error(exc) from exc


def read(query: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> list[dict]:
    """Run a read transaction; values keep driver types (Node, Relationship, Path)."""
    return _run(query, params or {}, write=False,
                timeout=timeout or get_settings().query_timeout_s)


def write(query: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> list[dict]:
    return _run(query, params or {}, write=True,
                timeout=timeout or get_settings().write_timeout_s)


def ping() -> dict[str, Any]:
    try:
        get_driver().verify_connectivity()
        return {"ok": True}
    except GraphDBError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(_map_error(exc))}
