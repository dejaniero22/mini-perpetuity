"""The owner's side of quarantine: list, approve, reject (INV-12). Callers commit.

Human CLI only. The MCP server must never import this module (a test enforces it).
"""

import psycopg
from psycopg.rows import class_row

from mini_perpetuity.access import gate
from mini_perpetuity.authz import Request
from mini_perpetuity.config import Config
from mini_perpetuity.ledger import append_event
from mini_perpetuity.memory import COLS, Memory
from mini_perpetuity.seatbelt import check_text

Conn = psycopg.Connection[tuple[object, ...]]

MAX_REASON_CHARS = 500


class ReviewError(ValueError):
    pass


def list_proposals(conn: Conn, cfg: Config, request: Request) -> list[Memory]:
    """Pending proposals, oldest first; the read is audited."""
    gate(conn, cfg, request, "review")
    rows = (
        conn.cursor(row_factory=class_row(Memory))
        .execute(f"SELECT {COLS} FROM memory_objects WHERE status = 'proposed' ORDER BY id")  # noqa: S608
        .fetchall()
    )
    append_event(conn, cfg, request.principal, "proposal.review", {"ids": [m.id for m in rows]})
    return rows


def decide(
    conn: Conn,
    cfg: Config,
    request: Request,
    memory_id: int,
    *,
    approve: bool,
    reason: str | None = None,
) -> Memory:
    """Approve or reject a pending proposal and record a `human_decision` event."""
    gate(conn, cfg, request, "approve" if approve else "reject")
    if memory_id < 1:
        raise ReviewError("id must be a positive integer")
    if not approve:
        reason = (reason or "").strip()
        if not reason:
            raise ReviewError("a reason is required to reject a proposal")
        if len(reason) > MAX_REASON_CHARS:
            raise ReviewError(f"reason is longer than {MAX_REASON_CHARS} characters")
        check_text(reason)  # the ledger is permanent
    row = (
        conn.cursor(row_factory=class_row(Memory))
        .execute(f"SELECT {COLS} FROM memory_objects WHERE id = %s FOR UPDATE", (memory_id,))  # noqa: S608
        .fetchone()
    )
    if row is None:
        raise ReviewError(f"no memory object {memory_id}")
    if row.status != "proposed":
        raise ReviewError(f"memory object {memory_id} is {row.status}, not a pending proposal")
    new_status = "approved" if approve else "rejected"
    conn.execute("UPDATE memory_objects SET status = %s WHERE id = %s", (new_status, memory_id))
    payload = {"decision": new_status, "id": memory_id, "reason": reason}
    payload["proposed_by"] = row.attributed_to_agent
    append_event(conn, cfg, request.principal, "human_decision", payload)
    return row
