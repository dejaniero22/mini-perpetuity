"""Ledger tests against a real PostgreSQL (MP_ADMIN_DSN). CI runs these on PostgreSQL 18."""

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from mini_perpetuity.admin import InitError, migrate_instance
from mini_perpetuity.cli import main
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect
from mini_perpetuity.ledger import append_event

Make = Callable[[], Config]


def admin_conn(admin_dsn: str, cfg: Config) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(make_conninfo(admin_dsn, dbname=cfg.dbname), autocommit=True)


def test_append_and_read(make_instance: Make) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c:
        eid = append_event(c, cfg, "rio", "note.created", {"k": [1, 2]})
    with app_connect(cfg) as c:
        row = c.execute(
            "SELECT actor, activity, payload, instance, occurred_at FROM events"
        ).fetchone()
    assert row is not None
    assert row[:4] == ("rio", "note.created", {"k": [1, 2]}, cfg.instance)
    assert row[4] is not None and eid == 1


@pytest.mark.parametrize(
    "stmt", ["UPDATE events SET actor = 'x'", "DELETE FROM events", "TRUNCATE events"]
)
def test_app_role_has_no_update_delete_truncate(make_instance: Make, stmt: str) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c:
        append_event(c, cfg, "rio", "a")
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(stmt)


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE events SET actor = 'x'",
        "UPDATE events SET actor = 'x' WHERE false",  # zero rows: still refused
        "DELETE FROM events",
        "DELETE FROM events WHERE false",
        "TRUNCATE events",
    ],
)
def test_trigger_blocks_even_with_table_privileges(
    make_instance: Make, admin_dsn: str, stmt: str
) -> None:
    cfg = make_instance()
    with admin_conn(admin_dsn, cfg) as a:
        a.execute(f"GRANT ALL ON events TO {cfg.user}")
    with app_connect(cfg) as c:
        append_event(c, cfg, "rio", "a")
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(stmt)
    with app_connect(cfg) as c:
        assert c.execute("SELECT count(*), min(actor) FROM events").fetchone() == (1, "rio")


@pytest.mark.parametrize(
    "stmt",
    [
        "ALTER TABLE events DISABLE TRIGGER events_append_only",
        "ALTER TABLE events DISABLE TRIGGER ALL",
        "DROP TRIGGER events_append_only ON events",
        "DROP TABLE events",
        "CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger "
        "LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'",
        "SET session_replication_role = replica",
    ],
)
def test_app_role_cannot_disable_or_replace_the_guard(
    make_instance: Make, admin_dsn: str, stmt: str
) -> None:
    cfg = make_instance()
    with admin_conn(admin_dsn, cfg) as a:  # even with every table privilege granted
        a.execute(f"GRANT ALL ON events TO {cfg.user}")
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(stmt)


def test_owner_can_disable_the_trigger_documented_limit(
    make_instance: Make, admin_dsn: str
) -> None:
    """MB3: layer 2 does not bind the table owner/superuser (layer 3 is out of scope)."""
    cfg = make_instance()
    with app_connect(cfg) as c:
        append_event(c, cfg, "rio", "a")
    with admin_conn(admin_dsn, cfg) as a:
        a.execute("ALTER TABLE events DISABLE TRIGGER events_append_only")
        a.execute("DELETE FROM events")
        assert a.execute("SELECT count(*) FROM events").fetchone() == (0,)


@pytest.mark.parametrize(
    "args",
    [
        ("rio", "a", {}, "other"),  # wrong instance
        ("", "a", {}, None),
        ("rio", "", {}, None),
    ],
)
def test_invalid_events_rejected(
    make_instance: Make, args: tuple[str, str, dict[str, object], str | None]
) -> None:
    cfg = make_instance()
    actor, activity, payload, instance = args
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute(
            "INSERT INTO events (actor, activity, payload, instance) VALUES (%s, %s, '{}', %s)",
            (actor, activity, instance or cfg.instance),
        )


def test_payload_must_be_json_object(make_instance: Make) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute(
            "INSERT INTO events (actor, activity, payload, instance) "
            "VALUES ('rio', 'a', '[1]', %s)",
            (cfg.instance,),
        )


def test_migrate_brings_an_old_instance_up_to_date(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    with admin_conn(admin_dsn, cfg) as a:  # simulate an instance created before M2
        a.execute("DROP TABLE events")
        a.execute("DROP FUNCTION events_append_only()")
        a.execute("DROP FUNCTION instance_name()")
        a.execute("DELETE FROM schema_migrations WHERE version = '0002_events.sql'")
    assert migrate_instance(admin_dsn, cfg.instance) == ["0002_events.sql"]
    with app_connect(cfg) as c:
        append_event(c, cfg, "rio", "a")
    assert migrate_instance(admin_dsn, cfg.instance) == []  # idempotent


def test_migrate_refuses_unknown_or_foreign_database(
    make_instance: Make, admin_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = make_instance()
    with pytest.raises(InitError):
        migrate_instance(admin_dsn, "Bad_Name")
    with admin_conn(admin_dsn, cfg) as a:
        a.execute("UPDATE instance_meta SET instance = 'zzz'")
    with pytest.raises(InitError, match="does not belong"):
        migrate_instance(admin_dsn, cfg.instance)
    monkeypatch.setenv("MP_ADMIN_DSN", admin_dsn)
    assert main(["admin", "migrate", cfg.instance]) == 2
