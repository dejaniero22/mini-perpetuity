"""Retrieval of approved memory (INV-6, INV-13). Callers commit."""

import psycopg
from psycopg.rows import class_row

from mini_perpetuity.access import gate
from mini_perpetuity.authz import Request
from mini_perpetuity.config import Config
from mini_perpetuity.ledger import append_event
from mini_perpetuity.memory import COLS, Memory
from mini_perpetuity.seatbelt import check_text

Conn = psycopg.Connection[tuple[object, ...]]

MAX_QUERY_CHARS = 500
MAX_LIMIT = 50
# Normal retrieval: approved, valid now, still the current version.
_CURRENT = (
    "status = 'approved' AND valid_at <= now() AND (invalid_at IS NULL OR invalid_at > now()) "
    "AND expired_at IS NULL"
)
# History: every version that was ever approved ("superseded" implies it was; see 0004).
_HISTORY = "status IN ('approved', 'superseded')"
# Only the owner may see sensitive objects; AI clients never do (ADR-M5).
_SENSITIVE_OK = "(%(sens)s OR sensitivity <> 'sensitive')"


class SearchError(ValueError):
    pass


def _validate(query: str, limit: int) -> str:
    q = query.strip()
    if not q:
        raise SearchError("query is empty")
    if len(q) > MAX_QUERY_CHARS:
        raise SearchError(f"query is longer than {MAX_QUERY_CHARS} characters")
    if "\x00" in q:
        raise SearchError("query contains a NUL character")
    if not 1 <= limit <= MAX_LIMIT:
        raise SearchError(f"limit must be between 1 and {MAX_LIMIT}")
    check_text(q)  # the ledger is permanent: never write a secret into it via the audit event
    return q


def search_memory(
    conn: Conn,
    cfg: Config,
    request: Request,
    query: str,
    *,
    history: bool = False,
    limit: int = 10,
) -> list[Memory]:
    """Authorize, search, and audit."""
    gate(conn, cfg, request, "search")
    q = _validate(query, limit)
    hits = (
        conn.cursor(row_factory=class_row(Memory))
        .execute(
            f"SELECT {COLS} FROM memory_objects "  # noqa: S608 - fixed fragments, q is a parameter
            f"WHERE search_vector @@ websearch_to_tsquery('english', %(q)s) "
            f"AND {_HISTORY if history else _CURRENT} AND {_SENSITIVE_OK} "
            "ORDER BY ts_rank(search_vector, websearch_to_tsquery('english', %(q)s)) DESC, id "
            "LIMIT %(n)s",
            {"q": q, "n": limit, "sens": request.principal == cfg.owner},
        )
        .fetchall()
    )
    append_event(
        conn, cfg, request.principal, "memory.search",
        {"query": q, "ids": [m.id for m in hits], "history": history, "limit": limit},
    )  # fmt: skip
    return hits


def get_memory(
    conn: Conn, cfg: Config, request: Request, memory_id: int, *, history: bool = False
) -> Memory | None:
    """Authorize, fetch one object by id (same visibility rules as search), and audit.

    An object the caller may not see is indistinguishable from one that does not exist: both
    return None (not an exception, so the audit event commits).
    """
    gate(conn, cfg, request, "get")
    if memory_id < 1:
        raise SearchError("id must be a positive integer")
    found = (
        conn.cursor(row_factory=class_row(Memory))
        .execute(
            f"SELECT {COLS} FROM memory_objects "  # noqa: S608
            f"WHERE id = %(id)s AND {_HISTORY if history else _CURRENT} AND {_SENSITIVE_OK}",
            {"id": memory_id, "sens": request.principal == cfg.owner},
        )
        .fetchone()
    )
    append_event(
        conn, cfg, request.principal, "memory.get",
        {"id": memory_id, "found": found is not None, "history": history},
    )  # fmt: skip
    return found
