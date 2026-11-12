"""mp correct and mp retire: changing approved memory is a trusted act (approver password)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from helpers import TEST_APPROVER_PASSWORD, run_cli, trusted_connect

from mini_perpetuity import cli
from mini_perpetuity.authz import Denied, Request
from mini_perpetuity.config import Config
from mini_perpetuity.db import LoginCheck, app_connect, approver_connect
from mini_perpetuity.memory import add_object
from mini_perpetuity.review import ReviewError, amend

Make = Callable[..., Config]
PW = TEST_APPROVER_PASSWORD


def fake_secret(name: str) -> str:
    """Looks like a credential assignment to the seatbelt, but is built at runtime so that secret
    scanners do not flag this file."""
    return f"{name} = " + "x" * 4 + "0" * 6


def approved(cfg: Config, content: str = "We use Postgres 16", **kw: object) -> int:
    with trusted_connect(cfg) as c:
        return add_object(
            c, cfg, epistemic_type="human_decision", content=content, agent="human:rio",
            activity="t", status="approved", **kw,  # type: ignore[arg-type]
        )  # fmt: skip


def rows(cfg: Config) -> list[tuple[int, str, str | None]]:
    with app_connect(cfg) as c:
        got = c.execute("SELECT id, status, content FROM memory_objects ORDER BY id").fetchall()
    return [(int(str(r[0])), str(r[1]), None if r[2] is None else str(r[2])) for r in got]


def events(cfg: Config, activity: str) -> list[dict[str, object]]:
    with app_connect(cfg) as c:
        got = c.execute(
            "SELECT payload, actor FROM events WHERE activity = %s ORDER BY id", (activity,)
        ).fetchall()
    return [{**(r[0] if isinstance(r[0], dict) else {}), "_actor": r[1]} for r in got]


def search_ids(cfg: Config, query: str, *extra: str) -> list[str]:
    out = run_cli(cfg, "search", query, *extra).stdout
    return [ln.split()[0] for ln in out.splitlines() if ln.startswith("#")]


# ---- correct ----


def test_correct_replaces_the_text_and_keeps_the_history(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    done = run_cli(
        cfg, "correct", str(old), "--content", "We use Postgres 18", "--reason", "version typo",
        passwords=[PW],
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert f"corrected #{old}" in done.stdout and "Postgres 16" in done.stdout  # shown first
    assert rows(cfg) == [
        (old, "superseded", "We use Postgres 16"),
        (old + 1, "approved", "We use Postgres 18"),
    ]
    assert search_ids(cfg, "Postgres") == [f"#{old + 1}"]  # only the new text is current
    assert search_ids(cfg, "Postgres", "--history") == [f"#{old}", f"#{old + 1}"]
    (event,) = events(cfg, "memory.correct")
    assert event["reason"] == "version typo" and event["supersedes"] == old
    assert event["_actor"] == cfg.owner and "retired" not in event


def test_correct_asks_for_the_password_and_changes_nothing_without_it(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    args = ("correct", str(old), "--content", "changed", "--reason", "r")
    no_tty = run_cli(cfg, *args)  # like an AI's shell tool: no terminal
    assert no_tty.returncode == 2 and "interactive terminal" in no_tty.stderr
    wrong = run_cli(cfg, *args, passwords=["not the password at all"])
    assert wrong.returncode == 2 and "approver login failed" in wrong.stderr
    empty = run_cli(cfg, *args, passwords=[""])
    assert empty.returncode == 2 and "no password entered" in empty.stderr
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]
    assert events(cfg, "memory.correct") == []


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--content", "   ", "--reason", "r"], "corrected text is empty"),
        (["--content", "x", "--reason", " "], "a reason is required"),
        (["--content", fake_secret("password"), "--reason", "r"], "ingest seatbelt"),
        (["--content", "x", "--reason", fake_secret("token")], "ingest seatbelt"),
    ],
)
def test_bad_input_is_refused_before_any_password_prompt(
    make_instance: Make, extra: list[str], message: str
) -> None:
    cfg = make_instance()
    old = approved(cfg)
    done = run_cli(cfg, "correct", str(old), *extra)  # no terminal: a prompt would error instead
    assert done.returncode == 2 and message in done.stderr
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]


def test_a_nul_character_is_refused_before_any_password_prompt(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    done = run_cli(
        cfg, "correct", str(old), "--content", "-", "--reason", "r", stdin_text="bad\x00text"
    )
    assert done.returncode == 2 and "NUL" in done.stderr
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]


def test_only_approved_current_notes_can_be_corrected(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    with app_connect(cfg) as c:
        pend = add_object(
            c, cfg, epistemic_type="human_decision", content="pending", agent="human:rio",
            activity="t", status="proposed",
        )  # fmt: skip
    run_cli(cfg, "correct", str(old), "--content", "v2", "--reason", "r", passwords=[PW])
    for target in (pend, old, 9999):  # pending, already superseded, missing
        done = run_cli(cfg, "correct", str(target), "--content", "v3", "--reason", "r")
        assert done.returncode == 2 and "no approved, current memory object" in done.stderr
    assert len(events(cfg, "memory.correct")) == 1


def test_correcting_to_the_same_text_still_makes_a_new_version_but_is_audited(
    make_instance: Make,
) -> None:
    cfg = make_instance()
    old = approved(cfg)
    done = run_cli(
        cfg, "correct", str(old), "--content", "We use Postgres 16", "--reason", "touch",
        passwords=[PW],
    )  # fmt: skip
    assert done.returncode == 0 and len(events(cfg, "memory.correct")) == 1


def test_content_can_be_read_from_stdin(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    done = run_cli(
        cfg, "correct", str(old), "--content", "-", "--reason", "r",
        stdin_text="from a pipe\nsecond line\n", passwords=None,
    )  # fmt: skip
    # stdin is the content here, so there is no terminal for the password: refused, nothing changed
    assert done.returncode == 2 and rows(cfg) == [(old, "approved", "We use Postgres 16")]


# ---- retire ----


def test_retire_stops_a_note_being_true_but_keeps_it_in_the_history(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    done = run_cli(cfg, "retire", str(old), "--reason", "no longer true", passwords=[PW])
    assert done.returncode == 0, done.stderr
    assert f"retired #{old}" in done.stdout
    assert search_ids(cfg, "Postgres") == []  # gone from normal search
    assert search_ids(cfg, "Postgres", "--history") == [f"#{old}", f"#{old + 1}"]
    (event,) = events(cfg, "memory.correct")
    assert event["retired"] is True and event["reason"] == "no longer true"


def test_a_future_dated_note_can_be_retired(make_instance: Make) -> None:
    cfg = make_instance()
    soon = datetime.now(UTC) + timedelta(days=3)
    old = approved(cfg, valid_at=soon)
    done = run_cli(cfg, "retire", str(old), "--reason", "cancelled", passwords=[PW])
    assert done.returncode == 0, done.stderr  # invalid_at must be after valid_at


def test_retire_needs_a_reason_and_a_password(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    assert run_cli(cfg, "retire", str(old), "--reason", "").returncode == 2
    assert run_cli(cfg, "retire", str(old), "--reason", "r").returncode == 2  # no terminal
    assert run_cli(cfg, "retire", str(old)).returncode == 2  # --reason is required by argparse
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]


# ---- who may do it ----


def test_the_everyday_role_cannot_amend_memory_directly(make_instance: Make) -> None:
    import psycopg

    cfg = make_instance()
    old = approved(cfg)
    owner = Request(cfg.owner, cfg.instance, "correct")
    with app_connect(cfg) as c, pytest.raises(psycopg.errors.Error):  # the database refuses
        amend(c, cfg, owner, old, content="sneaky", reason="r")
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]


def test_a_client_is_denied_and_the_denial_is_audited(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    with approver_connect(cfg, PW) as c:
        for action, retire in (("correct", False), ("retire", True)):
            req = Request("client:alpha", cfg.instance, action)
            with pytest.raises(Denied):
                amend(c, cfg, req, old, content=None if retire else "x", reason="r", retire=retire)
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]
    assert len(events(cfg, "authz.denied")) == 2


def test_amend_itself_requires_a_reason_for_both_actions(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    with approver_connect(cfg, PW) as c:
        for retire in (False, True):
            req = Request(cfg.owner, cfg.instance, "retire" if retire else "correct")
            with pytest.raises(ReviewError, match="reason is required"):
                amend(c, cfg, req, old, content=None if retire else "x", reason=None, retire=retire)
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]


def test_amend_itself_applies_the_seatbelt_to_new_text(make_instance: Make) -> None:
    from mini_perpetuity.seatbelt import Rejected

    cfg = make_instance()
    old = approved(cfg)
    with approver_connect(cfg, PW) as c, pytest.raises(Rejected):
        amend(
            c, cfg, Request(cfg.owner, cfg.instance, "correct"), old,
            content=fake_secret("api_key"), reason="r",
        )  # fmt: skip
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]


def test_amend_refuses_a_bad_id(make_instance: Make) -> None:
    cfg = make_instance()
    with approver_connect(cfg, PW) as c, pytest.raises(ReviewError):
        amend(c, cfg, Request(cfg.owner, cfg.instance, "correct"), 0, content="x", reason="r")


def test_amend_refuses_when_the_password_would_not_be_checked(
    make_instance: Make, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_instance()
    old = approved(cfg)
    monkeypatch.setattr(cli, "check_password_enforced", lambda *a: LoginCheck("accepted"))
    monkeypatch.setenv("MP_CONFIG", str(cfg.password_file.with_suffix(".toml")))
    assert cli.main(["retire", str(old), "--reason", "r"]) == 2
    assert "refusing to retire" in capsys.readouterr().err
    assert rows(cfg) == [(old, "approved", "We use Postgres 16")]


def test_the_ledger_payload_is_plain_json(make_instance: Make) -> None:
    cfg = make_instance()
    old = approved(cfg)
    run_cli(cfg, "retire", str(old), "--reason", "gone", passwords=[PW])
    json.dumps(events(cfg, "memory.correct"))  # no odd types leaked into the audit trail
