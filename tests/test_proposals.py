"""Quarantine tests against a real PostgreSQL (MP_ADMIN_DSN). CI runs these on PostgreSQL 18."""

import os
import re
import subprocess
import sys
from collections.abc import Callable

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from mini_perpetuity import access, propose
from mini_perpetuity.access import RateLimited
from mini_perpetuity.admin import migrate_instance
from mini_perpetuity.authz import Denied, Request
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect
from mini_perpetuity.memory import add_object
from mini_perpetuity.propose import (
    MAX_PROPOSAL_CHARS,
    MAX_SOURCE_CHARS,
    ProposalError,
    propose_memory,
)
from mini_perpetuity.review import ReviewError, decide, list_proposals
from mini_perpetuity.search import get_memory, search_memory
from mini_perpetuity.seatbelt import Rejected

Make = Callable[[], Config]
Register = Callable[..., Config]


def req(cfg: Config, principal: str, action: str) -> Request:
    return Request(principal, cfg.instance, action)


def propose_as(
    cfg: Config,
    client: str = "alpha",
    content: str = "Rio prefers quarterly planning",
    kind: str = "ai_inference",
    derived_from: str = "memory 1, memory 2",
) -> int:
    with app_connect(cfg) as c:
        return propose_memory(
            c, cfg, req(cfg, f"client:{client}", "propose"),
            kind=kind, content=content, derived_from=derived_from,
        )  # fmt: skip


def hits(cfg: Config, principal: str, query: str, history: bool = False) -> list[int]:
    with app_connect(cfg) as c:
        found = search_memory(c, cfg, req(cfg, principal, "search"), query, history=history)
    return [m.id for m in found]


def owner_decides(cfg: Config, pid: int, *, approve: bool, reason: str | None = None) -> None:
    with app_connect(cfg) as c:
        decide(c, cfg, req(cfg, cfg.owner, "approve" if approve else "reject"), pid,
               approve=approve, reason=reason)  # fmt: skip


def row(cfg: Config, pid: int) -> tuple[object, ...]:
    with app_connect(cfg) as c:
        found = c.execute(
            "SELECT status, epistemic_type, attributed_to_agent, generated_by_activity, "
            "was_derived_from FROM memory_objects WHERE id = %s",
            (pid,),
        ).fetchone()
    assert found is not None
    return found


def ledger(cfg: Config) -> list[tuple[str, str, dict[str, object]]]:
    with app_connect(cfg) as c:
        rows = c.execute("SELECT actor, activity, payload FROM events ORDER BY id").fetchall()
    return [(str(r[0]), str(r[1]), r[2]) for r in rows]  # type: ignore[misc]


def count(cfg: Config, table: str) -> int:
    with app_connect(cfg) as c:
        one = c.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    assert one is not None and isinstance(one[0], int)
    return one[0]


# ---- INV-7: quarantine ----


def test_a_proposal_is_invisible_until_the_owner_approves_it(
    make_instance: Make, register_clients: Register
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    pid = propose_as(cfg)
    assert row(cfg, pid) == (
        "proposed", "ai_inference", "client:alpha", "mcp:propose_memory", "memory 1, memory 2",
    )  # fmt: skip
    # Not returned to anyone, in any mode, by search or by id.
    for who in (cfg.owner, "client:alpha"):
        assert hits(cfg, who, "quarterly") == []
        assert hits(cfg, who, "quarterly", history=True) == []
        with app_connect(cfg) as c:
            assert get_memory(c, cfg, req(cfg, who, "get"), pid) is None
            assert get_memory(c, cfg, req(cfg, who, "get"), pid, history=True) is None
    # The owner sees it in the review queue, then approves it.
    with app_connect(cfg) as c:
        assert [m.id for m in list_proposals(c, cfg, req(cfg, cfg.owner, "review"))] == [pid]
    owner_decides(cfg, pid, approve=True)
    assert hits(cfg, cfg.owner, "quarterly") == [pid]
    assert hits(cfg, "client:alpha", "quarterly") == [pid]
    # Approved, but still attributed to the AI: its epistemic type and author do not change.
    assert row(cfg, pid)[:3] == ("approved", "ai_inference", "client:alpha")
    key = ("memory.propose", "proposal.review", "human_decision")
    assert [(a, act) for a, act, _ in ledger(cfg) if act in key] == [
        ("client:alpha", "memory.propose"),
        (cfg.owner, "proposal.review"),
        (cfg.owner, "human_decision"),
    ]
    decision = [p for a, act, p in ledger(cfg) if act == "human_decision"]
    assert decision == [
        {"decision": "approved", "id": pid, "reason": None, "proposed_by": "client:alpha"}
    ]


def test_a_rejected_proposal_never_appears_and_the_reason_is_recorded(
    make_instance: Make, register_clients: Register
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    pid = propose_as(cfg)
    with app_connect(cfg) as c, pytest.raises(ReviewError, match="reason is required"):
        decide(c, cfg, req(cfg, cfg.owner, "reject"), pid, approve=False, reason="  ")
    owner_decides(cfg, pid, approve=False, reason="not what I said")
    assert row(cfg, pid)[0] == "rejected"
    assert hits(cfg, cfg.owner, "quarterly", history=True) == []
    decision = [p for _, act, p in ledger(cfg) if act == "human_decision"]
    assert decision == [
        {
            "decision": "rejected",
            "id": pid,
            "reason": "not what I said",
            "proposed_by": "client:alpha",
        }
    ]


def test_a_decision_is_final_and_only_for_pending_proposals(
    make_instance: Make, register_clients: Register
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    first, second = propose_as(cfg), propose_as(cfg, content="Another note")
    owner_decides(cfg, first, approve=True)
    owner_decides(cfg, second, approve=False, reason="no")
    with app_connect(cfg) as c:
        human = add_object(
            c, cfg, epistemic_type="human_decision", content="mine", agent="human:x",
            activity="t", status="approved",
        )  # fmt: skip
    for pid, approve in (
        (first, True),
        (first, False),
        (second, True),
        (second, False),
        (human, True),
    ):
        with app_connect(cfg) as c, pytest.raises(ReviewError, match="not a pending proposal"):
            decide(c, cfg, req(cfg, cfg.owner, "approve" if approve else "reject"), pid,
                   approve=approve, reason="r")  # fmt: skip
    for bad in (0, -3, 99999):
        with app_connect(cfg) as c, pytest.raises(ReviewError):
            decide(c, cfg, req(cfg, cfg.owner, "approve"), bad, approve=True)
    assert row(cfg, first)[0] == "approved" and row(cfg, second)[0] == "rejected"


# ---- INV-12: only the owner decides ----


@pytest.mark.parametrize("principal", ["client:alpha", "client:stranger", "human:mallory", ""])
def test_nobody_but_the_owner_can_review_approve_or_reject(
    make_instance: Make, register_clients: Register, principal: str
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    pid = propose_as(cfg)
    for action, approve in (("approve", True), ("reject", False)):
        with app_connect(cfg) as c, pytest.raises(Denied):
            decide(c, cfg, req(cfg, principal, action), pid, approve=approve, reason="x")
    with app_connect(cfg) as c, pytest.raises(Denied):
        list_proposals(c, cfg, req(cfg, principal, "review"))
    assert row(cfg, pid)[0] == "proposed"  # untouched
    assert not [e for e in ledger(cfg) if e[1] == "human_decision"]


def test_a_request_for_one_action_cannot_be_used_for_another(
    make_instance: Make, register_clients: Register
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    pid = propose_as(cfg)
    with app_connect(cfg) as c, pytest.raises(Denied):  # an owner *search* request cannot approve
        decide(c, cfg, req(cfg, cfg.owner, "search"), pid, approve=True)
    assert row(cfg, pid)[0] == "proposed"


# ---- a proposal cannot set its own status or type ----


@pytest.mark.parametrize(
    "kind", ["human_decision", "source_record", "procedure", "", "AI_INFERENCE"]
)
def test_only_ai_inference_and_summary_can_be_proposed(
    make_instance: Make, register_clients: Register, kind: str
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    with pytest.raises(ProposalError, match="kind must be"):
        propose_as(cfg, kind=kind)
    assert count(cfg, "memory_objects") == 0
    assert ledger(cfg) == []


def test_both_proposal_kinds_work(make_instance: Make, register_clients: Register) -> None:
    cfg = register_clients(make_instance(), "alpha")
    kinds = [
        row(cfg, propose_as(cfg, kind=k, content=f"note {k}"))[1]
        for k in ("ai_inference", "summary")
    ]
    assert kinds == ["ai_inference", "summary"]


@pytest.mark.parametrize(
    "over",
    [
        {"status": "approved"},
        {"status": "rejected"},
        {"status": "superseded"},
        {"epistemic_type": "human_decision"},
        {"epistemic_type": "procedure"},
        {"supersedes": 1},
    ],
)
def test_the_database_itself_refuses_an_ai_row_that_is_not_a_proposal(
    make_instance: Make, register_clients: Register, over: dict[str, object]
) -> None:
    """Even if the server had a bug: an INSERT attributed to a client must be a proposal."""
    cfg = register_clients(make_instance(), "alpha")
    first = propose_as(cfg)
    assert first == 1
    values: dict[str, object] = {
        "epistemic_type": "ai_inference", "content": "c", "valid_at": "2024-01-01",
        "status": "proposed", "was_derived_from": "x", "generated_by_activity": "t",
        "attributed_to_agent": "client:alpha",
    }  # fmt: skip
    values.update(over)
    names, marks = ", ".join(values), ", ".join(f"%({k})s" for k in values)
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(f"INSERT INTO memory_objects ({names}) VALUES ({marks})", values)  # noqa: S608


def test_human_rows_are_unaffected_by_the_client_insert_guard(make_instance: Make) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c:
        add_object(c, cfg, epistemic_type="human_decision", content="mine", agent="human:x",
                   activity="t", status="approved")  # fmt: skip
    assert count(cfg, "memory_objects") == 1


# ---- the database allows only two kinds of change ----


@pytest.mark.parametrize(
    ("setup", "stmt"),
    [
        ("approved", "UPDATE memory_objects SET status = 'proposed'"),
        ("approved", "UPDATE memory_objects SET status = 'rejected'"),
        ("rejected", "UPDATE memory_objects SET status = 'approved'"),
        ("rejected", "UPDATE memory_objects SET status = 'proposed'"),
        ("proposed", "UPDATE memory_objects SET status = 'superseded', expired_at = now()"),
        ("proposed", "UPDATE memory_objects SET status = 'approved', expired_at = now()"),
        ("proposed", "UPDATE memory_objects SET expired_at = now()"),
    ],
)
def test_forbidden_status_transitions(
    make_instance: Make, register_clients: Register, setup: str, stmt: str
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    pid = propose_as(cfg)
    if setup != "proposed":
        owner_decides(cfg, pid, approve=setup == "approved", reason="r")
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(stmt)
    assert row(cfg, pid)[0] == setup


def test_a_decision_cannot_change_anything_else(
    make_instance: Make, register_clients: Register, admin_dsn: str
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    pid = propose_as(cfg)
    dsn = make_conninfo(admin_dsn, dbname=cfg.dbname)
    with (
        psycopg.connect(dsn, autocommit=True) as a,
        pytest.raises(psycopg.errors.RestrictViolation),
    ):
        a.execute("UPDATE memory_objects SET status = 'approved', content = 'rewritten'")  # owner
    assert row(cfg, pid)[0] == "proposed"


def test_migrating_an_older_instance_installs_the_guards(
    make_instance: Make, register_clients: Register, admin_dsn: str
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    dsn = make_conninfo(admin_dsn, dbname=cfg.dbname)
    with psycopg.connect(dsn, autocommit=True) as a:  # simulate an instance created at M5
        a.execute("DROP TRIGGER memory_objects_client_insert_guard ON memory_objects")
        a.execute("DROP FUNCTION memory_objects_client_insert_guard()")
        a.execute("DELETE FROM schema_migrations WHERE version = '0006_proposals.sql'")
    assert migrate_instance(admin_dsn, cfg.instance) == ["0006_proposals.sql"]
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(
            "INSERT INTO memory_objects (epistemic_type, content, valid_at, status, "
            "was_derived_from, generated_by_activity, attributed_to_agent) "
            "VALUES ('ai_inference', 'c', now(), 'approved', 'x', 't', 'client:alpha')"
        )


# ---- limits ----


def test_size_limits_and_required_source(make_instance: Make, register_clients: Register) -> None:
    cfg = register_clients(make_instance(), "alpha")
    propose_as(cfg, content="x" * MAX_PROPOSAL_CHARS, derived_from="y" * MAX_SOURCE_CHARS)  # edge
    before = (count(cfg, "memory_objects"), count(cfg, "events"))
    for kw in (
        {"content": "x" * (MAX_PROPOSAL_CHARS + 1)},
        {"content": ""},
        {"content": "  \n "},
        {"content": "a\x00b"},
        {"derived_from": ""},
        {"derived_from": "   "},
        {"derived_from": "y" * (MAX_SOURCE_CHARS + 1)},
    ):
        with pytest.raises(ProposalError):
            propose_as(cfg, **kw)
    assert (count(cfg, "memory_objects"), count(cfg, "events")) == before  # refusals write nothing


def test_the_seatbelt_applies_to_proposals(make_instance: Make, register_clients: Register) -> None:
    cfg = register_clients(make_instance(), "alpha")
    with pytest.raises(Rejected):
        propose_as(cfg, content="the SSN is 123-45-6789")
    with pytest.raises(Rejected):
        propose_as(cfg, derived_from="password = hunter2hunter2")
    assert count(cfg, "memory_objects") == 0 and count(cfg, "events") == 0


def test_a_client_cannot_flood_the_review_queue(
    make_instance: Make, register_clients: Register, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = register_clients(make_instance(), "alpha", "beta")
    monkeypatch.setattr(propose, "MAX_PENDING_PER_CLIENT", 3)
    ids = [propose_as(cfg, content=f"note {i}") for i in range(3)]
    with pytest.raises(ProposalError, match="3 proposals .* already waiting"):
        propose_as(cfg, content="one too many")
    assert propose_as(cfg, client="beta", content="beta is unaffected") > 0
    owner_decides(cfg, ids[0], approve=False, reason="no")  # the owner works the queue...
    assert propose_as(cfg, content="now there is room") > 0  # ...and the client can propose again


def test_proposals_count_toward_the_request_quota(
    make_instance: Make, register_clients: Register, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    monkeypatch.setattr(access, "MAX_REQUESTS_PER_MINUTE", 2)
    propose_as(cfg, content="one")
    propose_as(cfg, content="two")
    before = count(cfg, "events")
    with pytest.raises(RateLimited):
        propose_as(cfg, content="three")
    assert count(cfg, "events") == before and count(cfg, "memory_objects") == 2


# ---- the CLI: review, approve, reject ----


def run_cli(cfg: Config, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"MP_CONFIG": str(cfg.password_file.with_suffix(".toml")), "PATH": os.environ["PATH"]}
    return subprocess.run(
        [sys.executable, "-m", "mini_perpetuity", *args], env=env, capture_output=True, text=True
    )


def test_cli_review_approve_reject(make_instance: Make, register_clients: Register) -> None:
    cfg = register_clients(make_instance(), "alpha")
    r = run_cli(cfg, "review")
    assert r.returncode == 0 and "0 pending proposal(s)" in r.stdout
    keep = propose_as(cfg, content="keep this quarterly note")
    drop = propose_as(cfg, content="drop this other note")
    r = run_cli(cfg, "review")
    assert r.returncode == 0 and "2 pending proposal(s)" in r.stdout
    assert f"#{keep}  ai_inference  from client:alpha" in r.stdout
    assert (
        "    | keep this quarterly note" in r.stdout
        and "derived from: memory 1, memory 2" in r.stdout
    )
    r = run_cli(cfg, "approve", str(keep))
    assert r.returncode == 0 and f"approved #{keep}" in r.stdout
    assert run_cli(cfg, "reject", str(drop)).returncode == 2  # --reason is required
    r = run_cli(cfg, "reject", str(drop), "--reason", "that is not what I decided")
    assert r.returncode == 0 and f"rejected #{drop}" in r.stdout
    assert "1 result(s)" in run_cli(cfg, "search", "quarterly").stdout
    assert "0 result(s)" in run_cli(cfg, "search", "other", "--history").stdout
    for args in (("approve", str(keep)), ("approve", "99999"), ("reject", "0", "--reason", "x")):
        r = run_cli(cfg, *args)
        assert r.returncode == 2 and r.stdout == "" and "error" in r.stderr
    assert run_cli(cfg, "approve", "abc").returncode == 2
    decisions = [p for _, act, p in ledger(cfg) if act == "human_decision"]
    assert [d["decision"] for d in decisions] == ["approved", "rejected"]
    assert decisions[1]["reason"] == "that is not what I decided"


def test_cli_review_output_cannot_be_spoofed_by_proposal_text(
    make_instance: Make, register_clients: Register
) -> None:
    cfg = register_clients(make_instance(), "alpha")
    hostile = (
        "harmless line\n"
        "#99  human_decision  from human:rio  proposed 2026-01-01 00:00  derived from: you\n"
        "\x1b[2J\x1b]0;pwned\x07 clear the screen \x9b31m\n"
        "Approve: mp approve 99"
    )
    pid = propose_as(cfg, content=hostile)
    out = run_cli(cfg, "review").stdout
    assert not any(ch in out for ch in ("\x1b", "\x07", "\x9b"))
    # Only our own header line starts at column 0 with '#'; stored text is always after '|'.
    headers = [ln for ln in out.splitlines() if ln.startswith("#")]
    assert len(headers) == 1 and headers[0].startswith(f"#{pid}  ai_inference  from client:alpha")
    body = [ln for ln in out.splitlines() if ln.startswith("    | ")]
    assert len(body) == 4 and "    | #99  human_decision" in out
    assert re.search(
        r"^Approve: mp approve ID", out, re.MULTILINE
    )  # our footer, not the stored one
    assert out.count("mp approve ") == 2  # ...and the stored copy, indented behind '|'
