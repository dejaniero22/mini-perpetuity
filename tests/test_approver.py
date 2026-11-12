"""Separation of duties (ADR-M6b) against a real PostgreSQL (MP_ADMIN_DSN).

The everyday role (stored password) can read, create PENDING memory and write audit events. The
approver role (password typed by the human, stored nowhere) is the only one that can make memory
trusted, decide a proposal, or log a decision.
"""

from collections.abc import Callable
from functools import partial
from typing import Any

import psycopg
import pytest
from helpers import TEST_APPROVER_PASSWORD, run_cli, trusted_connect
from psycopg import sql

from mini_perpetuity import admin, cli
from mini_perpetuity.admin import InitError, migrate_instance, set_approver_password
from mini_perpetuity.authz import Request
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect, approver_connect, approver_is_set
from mini_perpetuity.ledger import append_event
from mini_perpetuity.memory import add_object, correct_object
from mini_perpetuity.review import ReviewError, decide

Make = Callable[..., Config]
PW = TEST_APPROVER_PASSWORD


def raw_insert(
    status: str, agent: str = "human:rio", kind: str = "human_decision"
) -> tuple[str, tuple[str, ...]]:
    return (
        "INSERT INTO memory_objects (epistemic_type, content, valid_at, status, was_derived_from, "
        "generated_by_activity, attributed_to_agent) VALUES (%s, 'c', now(), %s, 'src', 't', %s)",
        (kind, status, agent),
    )


def pending(cfg: Config, content: str = "a pending note") -> int:
    with app_connect(cfg) as c:
        return add_object(
            c, cfg, epistemic_type="human_decision", content=content, agent="human:rio",
            activity="mp add", status="proposed",
        )  # fmt: skip


def statuses(cfg: Config) -> list[object]:
    with app_connect(cfg) as c:
        rows = c.execute("SELECT status FROM memory_objects ORDER BY id").fetchall()
    return [r[0] for r in rows]


def decisions(cfg: Config) -> list[dict[str, object]]:
    with app_connect(cfg) as c:
        rows = c.execute("SELECT payload FROM events WHERE activity = 'human_decision' ORDER BY id")
        return [r[0] for r in rows.fetchall()]  # type: ignore[misc]


# ---- A. what the everyday role cannot do ----


@pytest.mark.parametrize("status", ["approved", "rejected", "superseded"])
def test_the_app_role_can_only_create_pending_memory(make_instance: Make, status: str) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(*raw_insert(status))
    assert statuses(cfg) == []
    with app_connect(cfg) as c:  # a pending note is fine, whatever name it carries
        c.execute(*raw_insert("proposed", agent="human:anyone"))
    assert statuses(cfg) == ["proposed"]


def test_the_app_role_cannot_change_any_memory(make_instance: Make) -> None:
    cfg = make_instance()
    with trusted_connect(cfg) as c:
        live = add_object(
            c, cfg, epistemic_type="human_decision", content="trusted", agent="human:rio",
            activity="t", status="approved",
        )  # fmt: skip
    todo = pending(cfg)
    for stmt, pid in (
        ("UPDATE memory_objects SET status = 'approved' WHERE id = %s", todo),
        ("UPDATE memory_objects SET status = 'rejected' WHERE id = %s", todo),
        ("UPDATE memory_objects SET status = 'superseded', expired_at = now() WHERE id = %s", live),
        ("UPDATE memory_objects SET content = 'x' WHERE id = %s", live),
    ):
        with app_connect(cfg) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(stmt, (pid,))
    assert statuses(cfg) == ["approved", "proposed"]


@pytest.mark.parametrize("activity", ["human_decision", "memory.correct"])
def test_the_app_role_cannot_forge_a_decision_or_correction_event(
    make_instance: Make, activity: str
) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        append_event(c, cfg, "human:rio", activity, {"decision": "approved", "id": 1})
    with trusted_connect(cfg) as c:  # the approver can
        append_event(c, cfg, "human:rio", activity, {"decision": "approved", "id": 1})


@pytest.mark.parametrize(
    "activity", ["memory.search", "memory.add", "memory.propose", "authz.denied"]
)
def test_the_app_role_still_writes_ordinary_audit_events(
    make_instance: Make, activity: str
) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c:
        append_event(c, cfg, "client:alpha", activity, {"k": 1})


@pytest.mark.parametrize("stmt", ["SET ROLE {r}", "SET SESSION AUTHORIZATION {r}"])
def test_the_app_role_cannot_become_the_approver(make_instance: Make, stmt: str) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(stmt.format(r=f'"{cfg.approver}"'))


def test_the_approver_can_do_what_the_app_role_cannot(make_instance: Make) -> None:
    cfg = make_instance()
    todo = pending(cfg)
    with trusted_connect(cfg) as c:
        first = add_object(
            c, cfg, epistemic_type="human_decision", content="v1", agent="human:rio",
            activity="t", status="approved",
        )  # fmt: skip
        second = correct_object(c, cfg, first, agent="human:rio", activity="t", content="v2")
        decide(c, cfg, Request(cfg.owner, cfg.instance, "approve"), todo, approve=True)
    assert statuses(cfg) == ["approved", "superseded", "approved"]  # todo, first, second
    assert second > first


# ---- B. the approver password ----


def test_the_approver_cannot_log_in_until_a_password_is_set(
    make_instance: Make, admin_dsn: str
) -> None:
    cfg = make_instance(approver=False)
    with pytest.raises(psycopg.OperationalError):
        approver_connect(cfg, PW)
    with app_connect(cfg) as c:
        assert approver_is_set(c, cfg) is False
    assert "approver=not set" in run_cli(cfg, "status").stdout
    set_approver_password(admin_dsn, cfg.instance, PW)
    with app_connect(cfg) as c:
        assert approver_is_set(c, cfg) is True
    assert "approver=set" in run_cli(cfg, "status").stdout
    approver_connect(cfg, PW).close()


def test_the_password_is_only_ever_stored_as_a_hash(
    make_instance: Make, admin_dsn: str, tmp_path: object
) -> None:
    cfg = make_instance()
    with psycopg.connect(admin_dsn) as a:
        row = a.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", (cfg.approver,)
        ).fetchone()
    assert row is not None
    stored = str(row[0])
    assert stored.startswith("SCRAM-SHA-256$") and PW not in stored
    # And this tool wrote it nowhere on disk (config, password file, anything beside them).
    for path in cfg.password_file.parent.iterdir():
        assert PW not in path.read_text(), f"password found in {path}"


def test_rotating_the_password_invalidates_the_old_one(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    new = "a completely different passphrase 7"
    set_approver_password(admin_dsn, cfg.instance, new)
    with pytest.raises(psycopg.OperationalError):
        approver_connect(cfg, PW)
    approver_connect(cfg, new).close()


@pytest.mark.parametrize(
    "weak",
    [
        "",  # nothing
        "short",  # too short
        "abcdef123456789",  # 15 characters: one too few
        "a" * 20,  # long enough but one repeated character
        "abababababababab",  # long enough but only two distinct characters
    ],
)
def test_weak_passwords_are_refused_and_change_nothing(
    make_instance: Make, admin_dsn: str, weak: str
) -> None:
    cfg = make_instance()
    with pytest.raises(InitError):
        set_approver_password(admin_dsn, cfg.instance, weak)
    approver_connect(cfg, PW).close()  # the old password still works


def test_a_missing_approver_role_is_explained(make_instance: Make, admin_dsn: str) -> None:
    with pytest.raises(InitError, match="no approver role"):
        set_approver_password(admin_dsn, "nosuchinstance", PW)


# ---- C. the CLI: terminal-only, batch, all-or-nothing ----


def add_notes(cfg: Config, n: int) -> None:
    for i in range(n):
        r = run_cli(cfg, "add", "--type", "human_decision", "--content", f"note number {i}")
        assert r.returncode == 0 and "pending" in r.stdout


def test_adding_never_needs_the_approver(make_instance: Make) -> None:
    cfg = make_instance(approver=False)  # not even set up
    add_notes(cfg, 2)
    assert statuses(cfg) == ["proposed", "proposed"]
    assert "2 pending item(s)" in run_cli(cfg, "review").stdout
    assert "0 result(s)" in run_cli(cfg, "search", "note").stdout  # pending is not searchable


def test_approving_without_a_set_up_approver_is_explained_before_any_prompt(
    make_instance: Make,
) -> None:
    cfg = make_instance(approver=False)
    add_notes(cfg, 1)
    r = run_cli(cfg, "approve", "1", passwords=[PW])
    assert r.returncode == 2 and "has not been set" in r.stderr
    assert "Approver password for" not in r.stderr and statuses(cfg) == ["proposed"]


def test_approval_needs_a_real_terminal_and_no_other_channel_works(make_instance: Make) -> None:
    cfg = make_instance()
    add_notes(cfg, 1)
    r = run_cli(cfg, "approve", "1")  # like a script, or an AI's shell tool: no terminal
    assert r.returncode == 2 and "interactive terminal" in r.stderr
    # Feeding the right password through every channel a script has changes nothing.
    r = run_cli(
        cfg, "approve", "1", stdin_text=PW + "\n",
        env={"MP_APPROVER_PASSWORD": PW, "PGPASSWORD": PW, "PGPASSFILE": "/dev/null"},
    )  # fmt: skip
    assert r.returncode == 2 and "interactive terminal" in r.stderr
    assert statuses(cfg) == ["proposed"] and decisions(cfg) == []


def test_a_wrong_password_is_refused_even_if_the_environment_has_the_right_one(
    make_instance: Make,
) -> None:
    cfg = make_instance()
    add_notes(cfg, 1)
    r = run_cli(
        cfg, "approve", "1", passwords=["not the real password at all"], env={"PGPASSWORD": PW}
    )
    assert r.returncode == 2 and "approver login failed" in r.stderr
    assert statuses(cfg) == ["proposed"] and decisions(cfg) == []


def test_an_empty_password_never_falls_back_to_a_stored_one(make_instance: Make) -> None:
    cfg = make_instance()
    add_notes(cfg, 1)
    r = run_cli(cfg, "approve", "1", passwords=[""], env={"PGPASSWORD": PW})  # libpq would use it
    assert r.returncode == 2 and "no password entered" in r.stderr
    assert statuses(cfg) == ["proposed"] and decisions(cfg) == []


def test_one_password_approves_a_whole_batch(make_instance: Make) -> None:
    cfg = make_instance()
    add_notes(cfg, 3)
    r = run_cli(cfg, "approve", "--all", passwords=[PW], timeout=30)
    assert r.returncode == 0 and "approved #1, #2, #3" in r.stdout
    assert r.stderr.count("Approver password for") == 1  # asked once, not once per item
    assert statuses(cfg) == ["approved"] * 3
    assert [d["id"] for d in decisions(cfg)] == [1, 2, 3]


def test_a_batch_rejection_takes_one_reason(make_instance: Make) -> None:
    cfg = make_instance()
    add_notes(cfg, 2)
    r = run_cli(cfg, "reject", "1", "2", "--reason", "duplicates", passwords=[PW], timeout=30)
    assert r.returncode == 0 and "rejected #1, #2" in r.stdout
    assert [(d["decision"], d["reason"]) for d in decisions(cfg)] == [
        ("rejected", "duplicates")
    ] * 2
    assert statuses(cfg) == ["rejected", "rejected"]


def test_a_batch_is_all_or_nothing(make_instance: Make) -> None:
    cfg = make_instance()
    first, second = pending(cfg, "one"), pending(cfg, "two")
    owner = Request(cfg.owner, cfg.instance, "approve")
    with pytest.raises(ReviewError), trusted_connect(cfg) as c:
        decide(c, cfg, owner, first, approve=True)
        decide(c, cfg, owner, 999_999, approve=True)  # fails: the whole transaction rolls back
    assert statuses(cfg) == ["proposed", "proposed"] and decisions(cfg) == []
    assert second > first


def test_duplicate_ids_are_decided_once(make_instance: Make) -> None:
    cfg = make_instance()
    add_notes(cfg, 1)
    r = run_cli(cfg, "approve", "1", "1", passwords=[PW], timeout=30)
    assert r.returncode == 0 and "approved #1" in r.stdout and "#1, #1" not in r.stdout
    assert len(decisions(cfg)) == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((), "give one or more ids"),
        (("--all", "1"), "not both"),
        (("77",), "not a pending item: #77"),
        (("1", "2"), "not a pending item: #2"),
    ],
)
def test_bad_selections_are_refused_before_any_password_is_asked(
    make_instance: Make, args: tuple[str, ...], message: str
) -> None:
    cfg = make_instance()
    add_notes(cfg, 1)
    r = run_cli(cfg, "approve", *args, passwords=[PW], timeout=30)
    assert r.returncode == 2 and message in r.stderr
    assert "Approver password for" not in r.stderr and statuses(cfg) == ["proposed"]


def test_approve_all_with_nothing_pending(make_instance: Make) -> None:
    cfg = make_instance()
    r = run_cli(cfg, "approve", "--all", passwords=[PW], timeout=30)
    assert r.returncode == 2 and "nothing is pending" in r.stderr


# ---- D. mp admin set-approver ----


def test_set_approver_cli(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance(approver=False)
    env = {"MP_ADMIN_DSN": admin_dsn}
    name = cfg.instance
    # Without a terminal, or without the admin connection, it refuses.
    assert "interactive terminal" in run_cli(cfg, "admin", "set-approver", name, env=env).stderr
    assert "MP_ADMIN_DSN" in run_cli(cfg, "admin", "set-approver", name, passwords=[PW]).stderr
    # A weak password or a typo changes nothing.
    r = run_cli(cfg, "admin", "set-approver", name, passwords=["short"], env=env)
    assert r.returncode == 2 and "at least 16" in r.stderr
    r = run_cli(cfg, "admin", "set-approver", name, passwords=[PW, PW + "x"], env=env)
    assert r.returncode == 2 and "differ" in r.stderr
    with app_connect(cfg) as c:
        assert approver_is_set(c, cfg) is False
    r = run_cli(cfg, "admin", "set-approver", name, passwords=[PW, PW], env=env)
    assert r.returncode == 0 and "stored nowhere" in r.stdout
    approver_connect(cfg, PW).close()


# ---- E. upgrading an instance that predates the approver ----


def test_migrating_an_instance_created_before_the_approver_existed(
    make_instance: Make, admin_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = admin._migrations
    monkeypatch.setattr(admin, "_migrations", lambda: [m for m in original() if "0007" not in m[0]])
    cfg = make_instance(approver=False)  # an M6-era instance: no approver role yet
    with app_connect(cfg) as c:  # the old model let the everyday role create trusted memory
        add_object(
            c, cfg, epistemic_type="human_decision", content="written under the old rules",
            agent="human:rio", activity="t", status="approved",
        )  # fmt: skip
    todo = pending(cfg, "pending under the old rules")
    monkeypatch.setattr(admin, "_migrations", original)
    assert migrate_instance(admin_dsn, cfg.instance) == ["0007_approver.sql"]
    # Existing data is intact and searchable by the owner...
    assert "written under the old rules" in run_cli(cfg, "search", "rules").stdout
    # ...the everyday role has lost the power to create trusted memory...
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(*raw_insert("approved"))
    # ...and the old pending item waits for the approver, who has to be set up first.
    assert "approver=not set" in run_cli(cfg, "status").stdout
    set_approver_password(admin_dsn, cfg.instance, PW)
    with trusted_connect(cfg) as c:
        decide(c, cfg, Request(cfg.owner, cfg.instance, "approve"), todo, approve=True)
    assert statuses(cfg) == ["approved", "approved"]


# ---- F. properties that need a closer look ----


def test_the_password_is_never_sent_to_the_server_as_text(
    make_instance: Make, admin_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a SCRAM hash is sent, so the password cannot appear in a SQL statement or server log."""
    cfg = make_instance(approver=False)
    sent: list[str] = []

    class Spy(psycopg.Cursor[tuple[object, ...]]):
        def execute(self, query: Any, params: Any = None, **kw: Any) -> Any:
            text = (
                query.as_string(self.connection)
                if isinstance(query, sql.Composable)
                else str(query)
            )
            sent.append(text + " " + repr(params))
            return super().execute(query, params, **kw)

    real_connect = psycopg.connect
    monkeypatch.setattr("psycopg.connect", partial(real_connect, cursor_factory=Spy))
    secret = "a very distinctive passphrase 12345"
    set_approver_password(admin_dsn, cfg.instance, secret)
    monkeypatch.undo()
    assert any("SCRAM-SHA-256$" in s for s in sent), "the hash should have been sent"
    assert not any(secret in s for s in sent), "the plaintext password was sent to the server"
    approver_connect(cfg, secret).close()  # and it really is the password


def test_the_cli_decides_a_batch_in_one_transaction(
    make_instance: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the second decision fails, the first is not left behind."""
    cfg = make_instance()
    pending(cfg, "one")
    pending(cfg, "two")
    calls: list[int] = []

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise ReviewError("simulated failure on the second item")
        return decide(*args, **kwargs)

    monkeypatch.setenv("MP_CONFIG", str(cfg.password_file.with_suffix(".toml")))
    monkeypatch.setattr("mini_perpetuity.cli.read_password", lambda prompt: PW)
    monkeypatch.setattr("mini_perpetuity.cli.decide", flaky)
    assert cli.main(["approve", "--all"]) == 2
    assert len(calls) == 2
    assert statuses(cfg) == ["proposed", "proposed"] and decisions(cfg) == []
