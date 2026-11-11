"""Gate every retrieval request: authorize, rate-limit, and audit denials (INV-5, INV-13).

Limits are counted from the ledger itself, so they survive server restarts.
shortcut: count-then-insert is not atomic, so concurrent requests can overshoot a limit slightly;
fine for one local user, revisit if the server ever serves many parallel callers.
"""

import psycopg

from mini_perpetuity.authz import Denied, Request, authorize
from mini_perpetuity.config import Config
from mini_perpetuity.ledger import append_event

Conn = psycopg.Connection[tuple[object, ...]]

MAX_REQUESTS_PER_MINUTE = 60  # per client principal (the owner is exempt)
MAX_DENIALS_PER_MINUTE = 20  # per instance; beyond this denials are still refused, not recorded


class RateLimited(Exception):
    pass


def _recent(conn: Conn, where: str, *params: object) -> int:
    row = conn.execute(
        f"SELECT count(*) FROM events WHERE occurred_at > now() - interval '1 minute' AND {where}",  # noqa: S608
        params,
    ).fetchone()
    return int(row[0]) if row is not None and isinstance(row[0], int) else 0


def gate(conn: Conn, cfg: Config, request: Request, action: str) -> None:
    """Return if allowed. Raise Denied (recorded, committed) or RateLimited (not recorded)."""
    try:
        if request.action != action:
            raise Denied(f"action {request.action!r} is not a {action}")
        authorize(cfg, request)
    except Denied as e:
        if _recent(conn, "activity = 'authz.denied'") >= MAX_DENIALS_PER_MINUTE:
            raise RateLimited("too many denied requests") from e  # keeps the ledger from flooding
        append_event(
            conn, cfg, request.principal or "unknown", "authz.denied",
            {"action": request.action, "reason": str(e)},
        )  # fmt: skip
        conn.commit()  # the refusal must survive the caller's rollback
        raise
    if request.principal != cfg.owner:
        n = _recent(
            conn, "actor = %s AND activity IN ('memory.search', 'memory.get')", request.principal
        )
        if n >= MAX_REQUESTS_PER_MINUTE:
            raise RateLimited(f"more than {MAX_REQUESTS_PER_MINUTE} requests in the last minute")
