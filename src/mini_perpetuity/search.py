"""Full-text retrieval over approved memory (INV-6, INV-13). Callers commit."""

import psycopg
from psycopg.rows import class_row

from mini_perpetuity.authz import Denied, Request, authorize
from mini_perpetuity.config import Config
from mini_perpetuity.ledger import append_event
from mini_perpetuity.memory import COLS, Memory
from mini_perpetuity.seatbelt import check_text

Conn = psycopg.Connection[tuple[object, ...]]

MAX_QUERY_CHARS = 500
MAX_LIMIT = 50
# Normal search: approved, valid now, still the current version.
_CURRENT = (
    "status = 'approved' AND valid_at <= now() AND (invalid_at IS NULL OR invalid_at > now()) "
    "AND expired_at IS NULL"
)
# History: every version that was ever approved ("superseded" implies it was; see 0004).
_HISTORY = "status IN ('approved', 'superseded')"


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
    """Authorize, search, and audit. A denied request is audited and committed before raising."""
    try:
        if request.action != "search":
            raise Denied(f"action {request.action!r} is not a search")
        authorize(cfg, request)
    except Denied as e:
        append_event(
            conn, cfg, request.principal or "unknown", "authz.denied",
            {"action": request.action, "reason": str(e)},
        )  # fmt: skip
        conn.commit()  # so the refusal is recorded even though the caller's work is rolled back
        raise
    q = _validate(query, limit)
    hits = (
        conn.cursor(row_factory=class_row(Memory))
        .execute(
            f"SELECT {COLS} FROM memory_objects "  # noqa: S608 - fixed fragments, q is a parameter
            f"WHERE search_vector @@ websearch_to_tsquery('english', %(q)s) "
            f"AND {_HISTORY if history else _CURRENT} "
            "ORDER BY ts_rank(search_vector, websearch_to_tsquery('english', %(q)s)) DESC, id "
            "LIMIT %(n)s",
            {"q": q, "n": limit},
        )
        .fetchall()
    )
    append_event(
        conn, cfg, request.principal, "memory.search",
        {"query": q, "ids": [m.id for m in hits], "history": history, "limit": limit},
    )  # fmt: skip
    return hits
