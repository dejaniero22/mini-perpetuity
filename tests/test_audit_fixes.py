"""Fixes from the repository audit: trust-login detection, owner identity, TOML escaping, hints."""

import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from helpers import TEST_APPROVER_PASSWORD, run_cli

from mini_perpetuity import admin, cli, doctor
from mini_perpetuity.admin import set_approver_password
from mini_perpetuity.backup import BackupError, make_backup, setup_hint
from mini_perpetuity.config import Config, load_config, toml_string
from mini_perpetuity.db import LoginCheck, app_connect, check_password_enforced, hba_fix
from mini_perpetuity.doctor import exit_code, run_doctor

Make = Callable[..., Config]
ACCEPTED = LoginCheck("accepted")


# ---- 1. the approver password must really be checked by PostgreSQL ----


def test_a_wrong_password_is_refused_by_a_normal_server(make_instance: Make) -> None:
    cfg = make_instance()
    for role in (cfg.user, cfg.approver):
        assert check_password_enforced(cfg.host, cfg.port, cfg.dbname, role).state == "enforced"


def test_an_unreachable_server_is_unknown_not_enforced(make_instance: Make) -> None:
    cfg = make_instance()
    res = check_password_enforced(cfg.host, 1, cfg.dbname, cfg.user)
    assert res.state == "unknown" and res.detail


def test_doctor_is_ok_when_passwords_are_enforced(make_instance: Make) -> None:
    check = {c.name: c for c in run_doctor(make_instance())}["password login"]
    assert check.status == "ok" and "approver" in check.detail


def test_doctor_fails_when_a_wrong_password_gets_in(
    make_instance: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_instance()
    monkeypatch.setattr(doctor, "check_password_enforced", lambda *a: ACCEPTED)
    checks = run_doctor(cfg)
    bad = {c.name: c for c in checks}["password login"]
    assert bad.status == "fail" and "WRONG password" in bad.fix and "scram-sha-256" in bad.fix
    assert exit_code(checks) == 1


def test_doctor_only_warns_when_it_cannot_tell(
    make_instance: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "check_password_enforced", lambda *a: LoginCheck("unknown", "x"))
    check = {c.name: c for c in run_doctor(make_instance())}["password login"]
    assert check.status == "warn"


def test_a_login_that_accepts_a_wrong_password_is_detected(
    make_instance: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fake:
        def close(self) -> None:
            pass

    cfg = make_instance()
    monkeypatch.setattr(psycopg, "connect", lambda **kw: Fake())  # a server set to "trust"
    assert check_password_enforced(cfg.host, cfg.port, cfg.dbname, cfg.approver) == ACCEPTED


def test_approve_and_reject_refuse_when_the_password_is_not_checked(
    make_instance: Make, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_instance()
    assert run_cli(cfg, "add", "--type", "human_decision", "--content", "a note").returncode == 0
    monkeypatch.setattr(cli, "check_password_enforced", lambda *a: ACCEPTED)
    monkeypatch.setenv("MP_CONFIG", str(cfg.password_file.with_suffix(".toml")))
    for argv in (["approve", "1"], ["reject", "1", "--reason", "no"]):
        # (the prompt is never reached, so no terminal is needed)
        assert cli.main(argv) == 2
        assert "refusing to" in capsys.readouterr().err  # not merely "no terminal"
    with app_connect(cfg) as c:
        row = c.execute("SELECT status FROM memory_objects").fetchone()
    assert row == ("proposed",)  # nothing was decided


def test_approve_still_works_on_a_normal_server(make_instance: Make) -> None:
    cfg = make_instance()
    run_cli(cfg, "add", "--type", "human_decision", "--content", "a note")
    done = run_cli(cfg, "approve", "1", passwords=[TEST_APPROVER_PASSWORD])
    assert done.returncode == 0 and "approved #1" in done.stdout


def test_set_approver_reports_when_the_password_would_not_be_checked(
    make_instance: Make, admin_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_instance(approver=False)
    monkeypatch.setattr(admin, "check_password_enforced", lambda *a: ACCEPTED)
    assert (
        set_approver_password(admin_dsn, cfg.instance, "a long approver passphrase 99") == ACCEPTED
    )
    monkeypatch.setenv("MP_ADMIN_DSN", admin_dsn)
    answers = iter(["a long approver passphrase 99"] * 2)
    monkeypatch.setattr(cli, "read_password", lambda prompt: next(answers))
    assert cli.main(["admin", "set-approver", cfg.instance]) == 1  # set, but loudly flagged


def test_the_fix_text_names_the_role_and_both_address_families() -> None:
    text = hba_fix("mp_x_approver")
    assert "mp_x_approver" in text and "127.0.0.1/32" in text and "::1/128" in text


# ---- 3. who you are comes from the config file's owner, not from $USER ----


def test_identity_ignores_user_and_logname(make_instance: Make) -> None:
    cfg = make_instance()
    forged = {"USER": "somebody", "LOGNAME": "ceo", "USERNAME": "ceo"}
    assert run_cli(cfg, "search", "anything", env=forged).returncode == 0  # still the owner
    done = run_cli(cfg, "add", "--type", "human_decision", "--content", "who wrote me", env=forged)
    assert done.returncode == 0, done.stderr
    with app_connect(cfg) as c:
        row = c.execute("SELECT attributed_to_agent FROM memory_objects").fetchone()
    assert row == (cfg.owner,)  # not "human:ceo"


# ---- 4. paths in TOML files ----


@pytest.mark.parametrize(
    "value",
    ["C:\\Users\\rio\\.config\\mp\\x.pw", '/Users/o"brien/x.pw', "/srv/üñí/\tx", "plain/path"],
)
def test_toml_string_round_trips_hard_paths(value: str) -> None:
    assert tomllib.loads(f"k = {toml_string(value)}")["k"] == value


def test_a_config_with_hard_paths_loads(
    make_instance: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_instance()
    toml = cfg.password_file.with_suffix(".toml")
    hard = tmp_path / 'we"ird\\dir'
    hard.mkdir()
    pw = hard / cfg.password_file.name
    pw.write_text(cfg.password_file.read_text())
    pw.chmod(0o600)
    text = toml.read_text().replace(
        f"password_file = {toml_string(cfg.password_file)}", f"password_file = {toml_string(pw)}"
    )
    toml.write_text(text)
    loaded = load_config({"MP_CONFIG": str(toml)})
    assert loaded.password_file == pw and replace(loaded, password_file=pw).password()


def test_init_instance_writes_a_loadable_config_in_a_hard_directory(
    admin_dsn: str, tmp_path: Path
) -> None:
    import secrets

    from mini_perpetuity.admin import init_instance

    name = "t" + secrets.token_hex(5)
    folder = tmp_path / 'a "quoted" \\ dir'
    try:
        path = init_instance(admin_dsn, name, folder)
        cfg = load_config({"MP_CONFIG": str(path)})
        assert cfg.password_file.parent == folder and len(cfg.password()) > 20
    finally:
        admin._drop(admin_dsn, name)


# ---- 5. the "not set up" message must be a command that exists ----


def test_the_backup_hint_uses_real_flags(make_instance: Make) -> None:
    cfg = make_instance()
    with pytest.raises(BackupError) as err:
        make_backup(cfg)
    assert setup_hint(cfg.instance) in str(err.value)
    help_text = run_cli(cfg, "admin", "backup-setup", "--help").stdout
    for flag in ("--backup-dir", "--key-file"):
        assert flag in help_text and flag in setup_hint(cfg.instance)
