"""Memory-object tests against a real PostgreSQL (MP_ADMIN_DSN). CI runs these on PostgreSQL 18."""

from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
import pytest
from helpers import TEST_APPROVER_PASSWORD, run_cli, trusted_connect
from psycopg.conninfo import make_conninfo

from mini_perpetuity.cli import main
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect
from mini_perpetuity.memory import EPISTEMIC_TYPES, add_object, as_of, correct_object
from mini_perpetuity.seatbelt import Rejected

Make = Callable[[], Config]
Conn = psycopg.Connection[tuple[object, ...]]


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s + "T00:00:00+00:00")


def admin_conn(admin_dsn: str, cfg: Config) -> Conn:
    return psycopg.connect(make_conninfo(admin_dsn, dbname=cfg.dbname), autocommit=True)


def add(conn: Conn, cfg: Config, **kw: object) -> int:
    args: dict[str, object] = {
        "epistemic_type": "human_decision",
        "content": "Use PostgreSQL",
        "agent": "human:test",
        "activity": "test",
        "status": "approved",
    }
    args.update(kw)
    return add_object(conn, cfg, **args)  # type: ignore[arg-type]


def insert_sql(**over: object) -> tuple[str, dict[str, object]]:
    row: dict[str, object] = {
        "epistemic_type": "human_decision",
        "content": "c",
        "valid_at": ts("2024-01-01"),
        "status": "approved",
        "generated_by_activity": "test",
        "attributed_to_agent": "human:test",
        "was_derived_from": None,
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join(f"%({k})s" for k in row)
    return f"INSERT INTO memory_objects ({cols}) VALUES ({marks})", row  # noqa: S608


# ---- the two timelines ----------------------------------------------------------------------


def test_valid_time_and_system_time_give_different_answers(
    make_instance: Make, admin_dsn: str
) -> None:
    """Synthetic history. The system first recorded "true since 2024-01-01" (known 2024-02-01);
    on 2024-06-01 it learned that was wrong: true only since 2024-03-01."""
    cfg = make_instance()
    with admin_conn(admin_dsn, cfg) as a:
        v1 = a.execute(
            "INSERT INTO memory_objects (epistemic_type, content, valid_at, created_at, status, "
            "generated_by_activity, attributed_to_agent) "
            "VALUES ('human_decision', 'v1', '2024-01-01', '2024-02-01', 'approved', 't', 'h') "
            "RETURNING id"
        ).fetchone()
        assert v1 is not None
        a.execute(
            "INSERT INTO memory_objects (epistemic_type, content, valid_at, created_at, status, "
            "supersedes, generated_by_activity, attributed_to_agent) "
            "VALUES ('human_decision', 'v2', '2024-03-01', '2024-06-01', 'approved', %s, 't', 'h')",
            (v1[0],),
        )
        a.execute(
            "UPDATE memory_objects SET expired_at = '2024-06-01', status = 'superseded' "
            "WHERE id = %s",
            (v1[0],),
        )
    with trusted_connect(cfg) as c:

        def contents(valid: str, known: str | None) -> list[str]:
            return [m.content for m in as_of(c, ts(valid), ts(known) if known else None)]

        # True on 2024-02-15: the old belief said yes, the corrected one says no.
        assert contents("2024-02-15", "2024-03-01") == ["v1"]
        assert contents("2024-02-15", "2024-07-01") == []
        # True on 2024-04-01: both beliefs say yes, but different versions.
        assert contents("2024-04-01", "2024-03-01") == ["v1"]
        assert contents("2024-04-01", "2024-07-01") == ["v2"]
        # Before the system knew anything (but valid time covers it): nothing.
        assert contents("2024-04-01", "2024-01-15") == []
        # "Now" = currently known versions only.
        assert contents("2024-04-01", None) == ["v2"]
        # Boundary: known exactly when v1 expired and v2 was created -> only v2.
        assert contents("2024-04-01", "2024-06-01") == ["v2"]


def test_correction_chain(make_instance: Make) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        v1 = add(c, cfg, content="first", valid_at=ts("2024-01-01"))
    with trusted_connect(cfg) as c:
        v2 = correct_object(c, cfg, v1, agent="human:test", activity="test", content="second")
    with trusted_connect(cfg) as c:
        old, new = (
            c.execute(
                "SELECT status, expired_at, created_at, supersedes, content, valid_at "
                "FROM memory_objects WHERE id = %s",
                (i,),
            ).fetchone()
            for i in (v1, v2)
        )
        assert old is not None and new is not None
        assert old[0] == "superseded" and old[1] == new[2]  # expiry instant == new version's birth
        assert new[3] == v1 and new[4] == "second" and new[5] == ts("2024-01-01")  # valid_at copied
        assert [m.id for m in as_of(c, ts("2024-06-01"))] == [v2]
        # Ending validity is also a correction (a new version), not an in-place update.
        v3 = correct_object(
            c, cfg, v2, agent="human:test", activity="test", invalid_at=ts("2024-09-01")
        )
        assert [m.id for m in as_of(c, ts("2024-06-01"))] == [v3]
        assert as_of(c, ts("2024-10-01")) == []
        with pytest.raises(ValueError, match="already superseded"):
            correct_object(c, cfg, v2, agent="human:test", activity="test", content="x")
        with pytest.raises(ValueError, match="no memory object"):
            correct_object(c, cfg, 999999, agent="human:test", activity="test", content="x")


# ---- constraints -------------------------------------------------------------------------------


def test_every_epistemic_type_is_accepted_and_unknown_rejected(make_instance: Make) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        for t in EPISTEMIC_TYPES:
            add(c, cfg, epistemic_type=t, derived_from="doc 1, section 2")
    with trusted_connect(cfg) as c:
        sql, row = insert_sql(epistemic_type="gossip")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(sql, row)


@pytest.mark.parametrize("t", ["source_record", "extracted_claim", "ai_inference", "summary"])
def test_derived_types_need_a_source_pointer(make_instance: Make, t: str) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        sql, row = insert_sql(epistemic_type=t)
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(sql, row)


@pytest.mark.parametrize(
    "over",
    [
        {"generated_by_activity": ""},
        {"generated_by_activity": None},
        {"attributed_to_agent": ""},
        {"attributed_to_agent": None},
        {"content": ""},
        {"status": "maybe"},
        {"sensitivity": "top-secret"},
        {"was_derived_from": ""},
        {"invalid_at": ts("2023-01-01")},  # before valid_at
        {"invalid_at": ts("2024-01-01")},  # empty interval
    ],
)
def test_invalid_rows_rejected(make_instance: Make, over: dict[str, object]) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        sql, row = insert_sql(**over)
        with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
            c.execute(sql, row)


# ---- append-only guard -------------------------------------------------------------------------


@pytest.mark.parametrize("col", ["id", "created_at", "expired_at"])
def test_app_role_cannot_set_system_columns_on_insert(make_instance: Make, col: str) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c:
        sql, row = insert_sql(**{col: 1 if col == "id" else ts("2020-01-01")})
        # id: refused earlier still, because the column is GENERATED ALWAYS.
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.GeneratedAlways)):
            c.execute(sql, row)


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE memory_objects SET content = 'tampered'",
        "UPDATE memory_objects SET valid_at = now()",
        "UPDATE memory_objects SET created_at = '2000-01-01'",
        "DELETE FROM memory_objects",
        "TRUNCATE memory_objects",
    ],
)
def test_app_role_cannot_edit_delete_or_truncate(make_instance: Make, stmt: str) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        add(c, cfg)
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(stmt)


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE memory_objects SET status = 'approved'",  # not an expiry
        "UPDATE memory_objects SET status = 'rejected'",
        "UPDATE memory_objects SET expired_at = now()",  # expiry without status superseded
        "UPDATE memory_objects SET expired_at = NULL, status = 'superseded'",
    ],
)
def test_trigger_allows_only_expiring_a_live_version(make_instance: Make, stmt: str) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        add(c, cfg)
    with (
        trusted_connect(cfg) as c,
        pytest.raises((psycopg.errors.RestrictViolation, psycopg.errors.CheckViolation)),
    ):
        c.execute(stmt)


def test_cannot_expire_twice_or_change_an_expired_version(make_instance: Make) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        v1 = add(c, cfg)
        correct_object(c, cfg, v1, agent="human:test", activity="test", content="new")
    with trusted_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(
            "UPDATE memory_objects SET expired_at = now() + interval '1 day', "
            "status = 'superseded' WHERE id = %s",
            (v1,),
        )


def test_two_versions_cannot_supersede_the_same_one(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        v1 = add(c, cfg)
        correct_object(c, cfg, v1, agent="human:test", activity="test", content="a")
    with trusted_connect(cfg) as c:
        sql, row = insert_sql(supersedes=v1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(sql, row)


# ---- seatbelt, ledger, CLI ---------------------------------------------------------------------


def count(cfg: Config, table: str) -> int:
    with app_connect(cfg) as c:
        row = c.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        assert row is not None and isinstance(row[0], int)
        return row[0]


def test_seatbelt_blocks_before_anything_is_written(make_instance: Make) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c, pytest.raises(Rejected):
        add(c, cfg, content="employee SSN 123-45-6789")
    with trusted_connect(cfg) as c, pytest.raises(Rejected):
        add(c, cfg, epistemic_type="source_record", derived_from="password = hunter2hunter2")
    assert count(cfg, "memory_objects") == 0 and count(cfg, "events") == 0


def test_add_and_correct_write_ledger_events_without_content(make_instance: Make) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        v1 = add(c, cfg, content="private thought")
        v2 = correct_object(c, cfg, v1, agent="human:test", activity="test", content="edited")
    with trusted_connect(cfg) as c:
        rows = c.execute("SELECT activity, payload::text FROM events ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["memory.add", "memory.correct"]
    assert str(v1) in str(rows[0][1]) and str(v2) in str(rows[1][1])
    assert "private thought" not in str(rows) and "edited" not in str(rows)


def test_failed_insert_rolls_back_its_ledger_event(make_instance: Make) -> None:
    cfg = make_instance()
    with pytest.raises(psycopg.errors.CheckViolation), trusted_connect(cfg) as c:
        add(c, cfg, activity="")  # violates a CHECK after validation passes
    assert count(cfg, "memory_objects") == 0 and count(cfg, "events") == 0


def test_cli_add_stores_a_pending_note_and_approval_makes_it_trusted(make_instance: Make) -> None:
    cfg = make_instance()
    r = run_cli(cfg, "add", "--type", "human_decision", "--content", "Use PostgreSQL")
    assert r.returncode == 0 and "added #1 as pending" in r.stdout and "mp approve 1" in r.stdout
    r = run_cli(
        cfg, "add", "--type", "source_record", "--content", "Retention is 7 years",
        "--source", "policy X section Y", "--valid-at", "2024-01-01T00:00:00+00:00",
        "--sensitivity", "sensitive",
    )  # fmt: skip
    assert r.returncode == 0  # adding never asks for a password

    def snapshot() -> list[tuple[object, ...]]:
        with trusted_connect(cfg) as c:
            return [
                (m.epistemic_type, m.status, m.sensitivity, m.was_derived_from,
                 m.generated_by_activity, m.attributed_to_agent.startswith("human:"))
                for m in as_of(c, datetime.now(UTC))
            ]  # fmt: skip

    assert snapshot() == [
        ("human_decision", "proposed", "normal", None, "mp add", True),
        ("source_record", "proposed", "sensitive", "policy X section Y", "mp add", True),
    ]
    approved = run_cli(cfg, "approve", "1", "2", passwords=[TEST_APPROVER_PASSWORD])
    assert approved.returncode == 0 and "approved #1, #2" in approved.stdout
    assert [row[1] for row in snapshot()] == ["approved", "approved"]


@pytest.mark.parametrize(
    "args",
    [
        ["--type", "ai_inference", "--content", "x"],  # AI types are not human-addable
        ["--type", "source_record", "--content", "x"],  # missing --source
        ["--type", "human_decision", "--content", "x", "--valid-at", "2024-01-01"],  # no offset
        ["--type", "human_decision", "--content", "SSN 123-45-6789"],  # seatbelt
        ["--type", "human_decision", "--content", ""],  # empty
    ],
)
def test_cli_add_refusals(make_instance: Make, args: list[str]) -> None:
    cfg = make_instance()
    r = run_cli(cfg, "add", *args)
    assert r.returncode != 0
    assert "123-45-6789" not in r.stderr + r.stdout
    assert count(cfg, "memory_objects") == 0


def test_cli_add_reads_stdin(make_instance: Make) -> None:
    cfg = make_instance()
    r = run_cli(cfg, "add", "--type", "human_decision", "--content", "-", stdin_text="from stdin\n")
    assert r.returncode == 0
    with trusted_connect(cfg) as c:
        assert [m.content for m in as_of(c, datetime.now(UTC))] == ["from stdin"]


def test_main_add_needs_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MP_CONFIG", raising=False)
    assert main(["add", "--type", "human_decision", "--content", "x"]) == 2
