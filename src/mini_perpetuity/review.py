"""The owner's side of quarantine: list, approve, reject (INV-12). Callers commit.

Human CLI only. The MCP server must never import this module (a test enforces it). `decide` must
be called on the approver's connection: the database refuses any other role (ADR-M6b).
"""

import psycopg
from psycopg.rows import class_row

from mini_perpetuity.access import gate
from mini_perpetuity.authz import Request
from mini_perpetuity.config import Config
from mini_perpetuity.ledger import append_event
from mini_perpetuity.memory import COLS, Memory, correct_object
from mini_perpetuity.seatbelt import check_text

Conn = psycopg.Connection[tuple[object, ...]]

MAX_REASON_CHARS = 500


class ReviewError(ValueError):
    pass


def check_reason(reason: str | None) -> str:
    """Validate a rejection reason (it goes into the permanent ledger)."""
    text = (reason or "").strip()
    if not text:
        raise ReviewError("a reason is required to reject a proposal")
    if len(text) > MAX_REASON_CHARS:
        raise ReviewError(f"reason is longer than {MAX_REASON_CHARS} characters")
    check_text(text)
    return text


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
        reason = check_reason(reason)
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


def get_amendable(conn: Conn, cfg: Config, request: Request, memory_id: int) -> Memory | None:
    """The live approved version of a note (even one dated in the future), or None; audited."""
    gate(conn, cfg, request, "get")
    found = (
        conn.cursor(row_factory=class_row(Memory))
        .execute(
            f"SELECT {COLS} FROM memory_objects "  # noqa: S608
            "WHERE id = %s AND status = 'approved' AND expired_at IS NULL "
            "AND (invalid_at IS NULL OR invalid_at > now())",
            (memory_id,),
        )
        .fetchone()
    )
    append_event(
        conn, cfg, request.principal, "memory.get",
        {"id": memory_id, "found": found is not None, "history": False, "for": "amend"},
    )  # fmt: skip
    return found


def check_new_content(content: str | None) -> str:
    """Validate replacement text for a correction."""
    text = (content or "").strip()
    if not text:
        raise ReviewError("the corrected text is empty")
    if "\x00" in text:
        raise ReviewError("the corrected text contains a NUL character")
    check_text(text)
    return text


def amend(
    conn: Conn,
    cfg: Config,
    request: Request,
    memory_id: int,
    *,
    content: str | None,
    reason: str | None,
    retire: bool = False,
) -> int:
    """Replace an approved note with a corrected version, or retire it. Returns the new id.

    Like `decide`, this must run on the approver's connection (the database refuses anyone else).
    The old version stays in the history: nothing is deleted or edited in place.
    """
    gate(conn, cfg, request, "retire" if retire else "correct")
    if memory_id < 1:
        raise ReviewError("id must be a positive integer")
    why = check_reason(reason)
    new_text = None if retire else check_new_content(content)
    return correct_object(
        conn, cfg, memory_id, agent=request.principal,
        activity="mp retire" if retire else "mp correct",
        content=new_text, retire=retire, reason=why,
    )  # fmt: skip
