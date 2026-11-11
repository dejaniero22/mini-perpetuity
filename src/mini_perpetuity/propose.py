"""AI write-back: a proposal is always quarantined (INV-7). Callers commit.

This module can only create `proposed` rows. Approving lives in `review.py`, which the MCP
server never imports.
"""

import psycopg

from mini_perpetuity.access import gate
from mini_perpetuity.authz import Request
from mini_perpetuity.config import Config
from mini_perpetuity.memory import add_object

Conn = psycopg.Connection[tuple[object, ...]]

PROPOSAL_TYPES = ("ai_inference", "summary")
MAX_PROPOSAL_CHARS = 2000  # a runaway client cannot flood the review queue with huge items
MAX_SOURCE_CHARS = 500
MAX_PENDING_PER_CLIENT = 50  # ...or with many small ones


class ProposalError(ValueError):
    pass


def _clean(name: str, value: str, limit: int) -> str:
    v = value.strip()
    if not v:
        raise ProposalError(f"{name} is empty")
    if len(v) > limit:
        raise ProposalError(f"{name} is longer than {limit} characters")
    if "\x00" in v:
        raise ProposalError(f"{name} contains a NUL character")
    return v


def propose_memory(
    conn: Conn, cfg: Config, request: Request, *, kind: str, content: str, derived_from: str
) -> int:
    """Authorize, validate, and store a `proposed` object attributed to the calling client."""
    gate(conn, cfg, request, "propose")
    if kind not in PROPOSAL_TYPES:
        raise ProposalError(f"kind must be one of {PROPOSAL_TYPES}")
    text = _clean("content", content, MAX_PROPOSAL_CHARS)
    source = _clean("derived_from", derived_from, MAX_SOURCE_CHARS)
    row = conn.execute(
        "SELECT count(*) FROM memory_objects "
        "WHERE status = 'proposed' AND attributed_to_agent = %s",
        (request.principal,),
    ).fetchone()
    if row is not None and isinstance(row[0], int) and row[0] >= MAX_PENDING_PER_CLIENT:
        raise ProposalError(
            f"{row[0]} proposals from this client are already waiting for the owner's review"
        )
    return add_object(
        conn,
        cfg,
        epistemic_type=kind,
        content=text,
        agent=request.principal,
        activity="mcp:propose_memory",
        status="proposed",  # never settable by the caller
        derived_from=source,
        audit_activity="memory.propose",
    )
