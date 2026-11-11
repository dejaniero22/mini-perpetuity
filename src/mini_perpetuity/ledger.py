import psycopg
from psycopg.types.json import Jsonb

from mini_perpetuity.config import Config


def append_event(
    conn: psycopg.Connection[tuple[object, ...]],
    cfg: Config,
    actor: str,
    activity: str,
    payload: dict[str, object] | None = None,
) -> int:
    """Insert one ledger event for this instance; the caller commits. Returns the event id."""
    row = conn.execute(
        "INSERT INTO events (actor, activity, payload, instance) VALUES (%s, %s, %s, %s) "
        "RETURNING id",
        (actor, activity, Jsonb(payload or {}), cfg.instance),
    ).fetchone()
    if row is None or not isinstance(row[0], int):
        raise RuntimeError("INSERT ... RETURNING id returned no integer id")
    return row[0]
