"""Memory objects: bi-temporal versions with provenance. Callers commit."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import class_row

from mini_perpetuity.config import Config
from mini_perpetuity.ledger import append_event
from mini_perpetuity.seatbelt import check_text

Conn = psycopg.Connection[tuple[object, ...]]

EPISTEMIC_TYPES = (
    "source_record",
    "extracted_claim",
    "ai_inference",
    "human_decision",
    "procedure",
    "experience",
    "summary",
)
HUMAN_TYPES = ("human_decision", "source_record")  # what `mp add` may create
COLS = (
    "id, epistemic_type, content, valid_at, invalid_at, created_at, expired_at, status, "
    "sensitivity, supersedes, was_derived_from, generated_by_activity, attributed_to_agent"
)


@dataclass(frozen=True)
class Memory:
    id: int
    epistemic_type: str
    content: str
    valid_at: datetime
    invalid_at: datetime | None
    created_at: datetime
    expired_at: datetime | None
    status: str
    sensitivity: str
    supersedes: int | None
    was_derived_from: str | None
    generated_by_activity: str
    attributed_to_agent: str


def _insert(conn: Conn, row: dict[str, object]) -> int:
    cols = ", ".join(row)
    marks = ", ".join(f"%({k})s" for k in row)
    got = conn.execute(f"INSERT INTO memory_objects ({cols}) VALUES ({marks}) RETURNING id", row)  # noqa: S608 - column names are fixed by callers
    one = got.fetchone()
    if one is None or not isinstance(one[0], int):
        raise RuntimeError("INSERT ... RETURNING id returned no integer id")
    return one[0]


def add_object(
    conn: Conn,
    cfg: Config,
    *,
    epistemic_type: str,
    content: str,
    agent: str,
    activity: str,
    status: str,
    valid_at: datetime | None = None,
    derived_from: str | None = None,
    sensitivity: str = "normal",
    audit_activity: str = "memory.add",
) -> int:
    """Insert a first version and write a ledger event (no content in the event)."""
    check_text(content, derived_from)
    oid = _insert(
        conn,
        {
            "epistemic_type": epistemic_type,
            "content": content,
            "valid_at": valid_at or datetime.now(UTC),
            "status": status,
            "sensitivity": sensitivity,
            "was_derived_from": derived_from,
            "generated_by_activity": activity,
            "attributed_to_agent": agent,
        },
    )
    append_event(conn, cfg, agent, audit_activity, {"id": oid, "type": epistemic_type})
    return oid


def correct_object(
    conn: Conn,
    cfg: Config,
    old_id: int,
    *,
    agent: str,
    activity: str,
    content: str | None = None,
    valid_at: datetime | None = None,
    invalid_at: datetime | None = None,
    retire: bool = False,
    reason: str | None = None,
) -> int:
    """Write a new version of a live object and expire the old one. Returns the new id.

    `retire` makes the new version stop being true now (it is no longer found by normal search,
    only by history). `reason` goes into the ledger event.
    """
    old = (
        conn.cursor(row_factory=class_row(Memory))
        .execute(
            f"SELECT {COLS} FROM memory_objects WHERE id = %s FOR UPDATE",  # noqa: S608
            (old_id,),
        )
        .fetchone()
    )
    if old is None:
        raise ValueError(f"no memory object {old_id}")
    if old.expired_at is not None:
        raise ValueError(f"memory object {old_id} is already superseded")
    if old.status != "approved":  # superseded must mean "was approved" (see migration 0004)
        raise ValueError(
            f"memory object {old_id} is {old.status}; only approved objects can be corrected"
        )
    if retire:  # never earlier than just after the version starts (the table requires it)
        invalid_at = max(datetime.now(UTC), old.valid_at + timedelta(microseconds=1))
    new_content = old.content if content is None else content
    check_text(new_content)
    new_id = _insert(
        conn,
        {
            "epistemic_type": old.epistemic_type,
            "content": new_content,
            "valid_at": valid_at or old.valid_at,
            "invalid_at": invalid_at if invalid_at is not None else old.invalid_at,
            "status": old.status,
            "sensitivity": old.sensitivity,
            "supersedes": old_id,
            "was_derived_from": old.was_derived_from,
            "generated_by_activity": activity,
            "attributed_to_agent": agent,
        },
    )
    conn.execute(
        "UPDATE memory_objects SET expired_at = now(), status = 'superseded' WHERE id = %s",
        (old_id,),
    )
    payload: dict[str, object] = {"id": new_id, "supersedes": old_id}
    if retire:
        payload["retired"] = True
    if reason:
        payload["reason"] = reason
    append_event(conn, cfg, agent, "memory.correct", payload)
    return new_id


def as_of(conn: Conn, valid_on: datetime, known_on: datetime | None = None) -> list[Memory]:
    """Versions true on `valid_on` as the system knew them on `known_on` (default: now)."""
    cur = conn.cursor(row_factory=class_row(Memory))
    return cur.execute(
        f"SELECT {COLS} FROM memory_objects "  # noqa: S608
        "WHERE valid_at <= %(v)s AND (invalid_at IS NULL OR invalid_at > %(v)s) "
        "AND created_at <= COALESCE(%(k)s::timestamptz, now()) "
        "AND (expired_at IS NULL OR expired_at > COALESCE(%(k)s::timestamptz, now())) "
        "ORDER BY id",
        {"v": valid_on, "k": known_on},
    ).fetchall()
