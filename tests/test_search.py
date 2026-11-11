"""Retrieval tests against a real PostgreSQL (MP_ADMIN_DSN). CI runs these on PostgreSQL 18."""

import os
import subprocess
import sys
from collections.abc import Callable

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from mini_perpetuity.admin import migrate_instance
from mini_perpetuity.authz import Denied, Request
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect
from mini_perpetuity.memory import add_object, correct_object
from mini_perpetuity.search import MAX_QUERY_CHARS, SearchError, search_memory
from mini_perpetuity.seatbelt import Rejected

Make = Callable[[], Config]
Conn = psycopg.Connection[tuple[object, ...]]


def req(cfg: Config, **over: str) -> Request:
    args = {"principal": cfg.owner, "instance": cfg.instance, "action": "search"}
    args.update(over)
    return Request(**args)


def search(
    cfg: Config,
    query: str,
    *,
    request: Request | None = None,
    history: bool = False,
    limit: int = 10,
) -> list[int]:
    with app_connect(cfg) as c:
        hits = search_memory(c, cfg, request or req(cfg), query, history=history, limit=limit)
    return [m.id for m in hits]


def add(cfg: Config, content: str, **kw: object) -> int:
    args: dict[str, object] = {
        "epistemic_type": "human_decision",
        "agent": "human:test",
        "activity": "test",
        "status": "approved",
    }
    args.update(kw)
    with app_connect(cfg) as c:
        return add_object(c, cfg, content=content, **args)  # type: ignore[arg-type]


def admin_insert(admin_dsn: str, cfg: Config, content: str, status: str, **cols: str) -> int:
    """Rows the app role cannot create (proposed/rejected/future/ended): the owner inserts them."""
    row = {
        "epistemic_type": "ai_inference",
        "content": content,
        "valid_at": "2024-01-01",
        "status": status,
        "was_derived_from": "doc 1",
        "generated_by_activity": "test",
        "attributed_to_agent": "client:test",
    }
    row.update(cols)
    names, marks = ", ".join(row), ", ".join(f"%({k})s" for k in row)
    dsn = make_conninfo(admin_dsn, dbname=cfg.dbname)
    with psycopg.connect(dsn, autocommit=True) as a:
        got = a.execute(f"INSERT INTO memory_objects ({names}) VALUES ({marks}) RETURNING id", row)  # noqa: S608
        one = got.fetchone()
    assert one is not None and isinstance(one[0], int)
    return one[0]


def events(cfg: Config, activity: str | None = None) -> list[tuple[str, str, dict[str, object]]]:
    with app_connect(cfg) as c:
        rows = c.execute(
            "SELECT actor, activity, payload FROM events "
            "WHERE %(a)s::text IS NULL OR activity = %(a)s ORDER BY id",
            {"a": activity},
        ).fetchall()
    return [(str(r[0]), str(r[1]), r[2]) for r in rows]  # type: ignore[misc]


# ---- INV-6: what may come back ----


def test_only_approved_current_versions_come_back(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    ok1 = add(cfg, "PostgreSQL stores the memory")
    ok2 = add(cfg, "PostgreSQL full text search is built in")
    add(cfg, "Unrelated cooking recipe")
    admin_insert(admin_dsn, cfg, "PostgreSQL proposed plan", "proposed")
    admin_insert(admin_dsn, cfg, "PostgreSQL rejected idea", "rejected")
    future = admin_insert(
        admin_dsn, cfg, "PostgreSQL future fact", "approved", valid_at="2099-01-01"
    )
    ended = admin_insert(
        admin_dsn, cfg, "PostgreSQL ended fact", "approved", valid_at="2020-01-01",
        invalid_at="2021-01-01",
    )  # fmt: skip
    # Approved but already expired (system time): no longer the current version.
    expired = admin_insert(
        admin_dsn, cfg, "PostgreSQL retired fact", "approved", created_at="2024-01-01",
        expired_at="2024-06-01",
    )  # fmt: skip
    assert sorted(search(cfg, "postgresql")) == [ok1, ok2]
    # History adds approved versions outside their windows, but never proposed/rejected.
    assert sorted(search(cfg, "postgresql", history=True)) == sorted(
        [ok1, ok2, future, ended, expired]
    )


def test_expired_versions_only_in_history(make_instance: Make) -> None:
    cfg = make_instance()
    v1 = add(cfg, "alpha beta")
    with app_connect(cfg) as c:
        v2 = correct_object(c, cfg, v1, agent="human:test", activity="test", content="alpha gamma")
    assert search(cfg, "beta") == []
    assert search(cfg, "beta", history=True) == [v1]
    assert search(cfg, "gamma") == [v2]
    assert sorted(search(cfg, "alpha")) == [v2]
    assert sorted(search(cfg, "alpha", history=True)) == [v1, v2]


def test_unapproved_versions_cannot_be_superseded(make_instance: Make, admin_dsn: str) -> None:
    """So history can never surface unapproved text: 'superseded' implies 'was approved'."""
    cfg = make_instance()
    pid = admin_insert(admin_dsn, cfg, "proposed thing", "proposed")
    with app_connect(cfg) as c:
        with pytest.raises(ValueError, match="only approved objects can be corrected"):
            correct_object(c, cfg, pid, agent="human:test", activity="test", content="x")
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(
            "UPDATE memory_objects SET expired_at = now(), status = 'superseded' WHERE id = %s",
            (pid,),
        )


def test_ranking_prefers_more_matching_terms(make_instance: Make) -> None:
    cfg = make_instance()
    one = add(cfg, "PostgreSQL is a database")
    both = add(cfg, "PostgreSQL storage and more storage")
    assert search(cfg, "postgresql or storage") == [both, one]
    assert search(cfg, "postgresql or storage", limit=1) == [both]


def test_search_syntax(make_instance: Make) -> None:
    cfg = make_instance()
    a = add(cfg, "PostgreSQL stores data")
    b = add(cfg, "MySQL stores data")
    assert sorted(search(cfg, "stores data")) == [a, b]
    assert search(cfg, "stores -mysql") == [a]
    assert search(cfg, '"stores data"') == sorted([a, b]) or sorted(
        search(cfg, '"stores data"')
    ) == [a, b]
    assert search(cfg, "the and of") == []  # stop words only: no error, no hits


# ---- malformed input, injection ----

WEIRD = [
    "'; DROP TABLE memory_objects; --",
    "' OR '1'='1",
    '"unbalanced quote',
    "a & | ! (",
    "((((",
    "x:*",
    "%s %(q)s",
    "{app_role}",
    "\\",
    "postgresql\n-- comment",
    "😀 emoji",
    "!!!",
    "-",
    '""',
    "or or or",
]


@pytest.mark.parametrize("query", WEIRD)
def test_weird_queries_are_data_not_code(make_instance: Make, query: str) -> None:
    cfg = make_instance()
    add(cfg, "PostgreSQL stores the memory")
    search(cfg, query)  # must not raise
    assert search(cfg, "postgresql") != []  # table intact
    stored = [e[2]["query"] for e in events(cfg, "memory.search")]
    assert query.strip() in stored  # audited verbatim, as a value


@pytest.mark.parametrize(
    ("query", "limit"),
    [
        ("", 10),
        ("   \n\t", 10),
        ("x" * (MAX_QUERY_CHARS + 1), 10),
        ("a\x00b", 10),
        ("postgresql", 0),
        ("postgresql", -1),
        ("postgresql", 51),
    ],
)
def test_invalid_requests_rejected_and_not_audited(
    make_instance: Make, query: str, limit: int
) -> None:
    cfg = make_instance()
    with pytest.raises(SearchError):
        search(cfg, query, limit=limit)
    assert events(cfg) == []


def test_boundary_length_accepted(make_instance: Make) -> None:
    cfg = make_instance()
    assert search(cfg, "x" * MAX_QUERY_CHARS) == []
    assert search(cfg, "postgresql", limit=50) == []


def test_secret_shaped_query_never_reaches_the_ledger(make_instance: Make) -> None:
    cfg = make_instance()
    with pytest.raises(Rejected):
        search(cfg, "password = hunter2hunter2")
    assert events(cfg) == []


# ---- INV-13: audit ----


def test_every_search_is_audited(make_instance: Make) -> None:
    cfg = make_instance()
    a = add(cfg, "PostgreSQL stores the memory")
    assert search(cfg, "postgresql") == [a]
    assert search(cfg, "nothing matches this", history=True) == []
    audit = events(cfg, "memory.search")
    assert [(e[0], e[2]) for e in audit] == [
        (cfg.owner, {"query": "postgresql", "ids": [a], "history": False, "limit": 10}),
        (cfg.owner, {"query": "nothing matches this", "ids": [], "history": True, "limit": 10}),
    ]


# ---- INV-5: default deny ----


@pytest.mark.parametrize(
    "over",
    [
        {"principal": "human:mallory"},
        {"principal": "client:claude"},
        {"principal": ""},
        {"instance": "someoneelse"},
        {"action": "write"},
    ],
)
def test_denied_requests_return_nothing_and_are_audited(
    make_instance: Make, over: dict[str, str]
) -> None:
    cfg = make_instance()
    add(cfg, "PostgreSQL stores the memory")
    with pytest.raises(Denied):
        search(cfg, "postgresql", request=req(cfg, **over))
    # The refusal is recorded (committed) even though the caller's transaction failed.
    denied = events(cfg, "authz.denied")
    assert len(denied) == 1
    assert denied[0][0] == (over.get("principal", cfg.owner) or "unknown")
    assert events(cfg, "memory.search") == []


def test_denial_comes_before_validation(make_instance: Make) -> None:
    cfg = make_instance()
    with pytest.raises(Denied):  # not SearchError: a stranger learns nothing about query rules
        search(cfg, "", request=req(cfg, principal="human:mallory"))


# ---- upgrade path and CLI ----


def test_migrate_adds_search_to_an_existing_instance(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    a = add(cfg, "PostgreSQL stores the memory")
    dsn = make_conninfo(admin_dsn, dbname=cfg.dbname)
    with psycopg.connect(dsn, autocommit=True) as c:  # simulate an instance created at M3
        c.execute("DROP INDEX memory_objects_search_idx")
        c.execute("ALTER TABLE memory_objects DROP COLUMN search_vector")
        c.execute("DELETE FROM schema_migrations WHERE version = '0004_search.sql'")
    assert migrate_instance(admin_dsn, cfg.instance) == ["0004_search.sql"]
    assert search(cfg, "postgresql") == [a]  # existing rows are searchable after the upgrade


def run_cli(cfg: Config, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"MP_CONFIG": str(cfg.password_file.with_suffix(".toml")), "PATH": os.environ["PATH"]}
    return subprocess.run(
        [sys.executable, "-m", "mini_perpetuity", *args], env=env, capture_output=True, text=True
    )


def test_cli_search(make_instance: Make) -> None:
    cfg = make_instance()
    run_cli(cfg, "add", "--type", "human_decision", "--content", "Use PostgreSQL for storage.")
    run_cli(
        cfg, "add", "--type", "human_decision", "--content", "PostgreSQL is also the search engine"
    )
    r = run_cli(cfg, "search", "postgresql")
    assert r.returncode == 0 and "2 result(s)" in r.stdout
    assert "human_decision  approved" in r.stdout and "    Use PostgreSQL for storage." in r.stdout
    assert "1 result(s)" in run_cli(cfg, "search", "postgresql", "--limit", "1").stdout
    r = run_cli(cfg, "search", "nothing matches", "--history")
    assert r.returncode == 0 and "0 result(s)" in r.stdout
    assert len(events(cfg, "memory.search")) == 3


@pytest.mark.parametrize(
    "args",
    [
        [""],
        ["x", "--limit", "0"],
        ["x", "--limit", "99"],
        ["x", "--limit", "many"],
        ["token = s3cr3tvalue"],
    ],
)
def test_cli_search_refusals(make_instance: Make, args: list[str]) -> None:
    cfg = make_instance()
    r = run_cli(cfg, "search", *args)
    assert r.returncode != 0 and "s3cr3tvalue" not in r.stdout + r.stderr
    assert events(cfg) == []


def test_cli_neutralizes_terminal_escapes_in_stored_text(make_instance: Make) -> None:
    cfg = make_instance()
    add(cfg, "alert \x1b[31mRED\x1b]0;pwned\x07 and \x9b2J end")
    out = run_cli(cfg, "search", "alert").stdout
    assert "RED" in out and "end" in out
    assert not any(ch in out for ch in ("\x1b", "\x07", "\x9b"))
