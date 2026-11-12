"""mp doctor and the backup commands, end to end (real PostgreSQL, real subprocesses)."""

import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from helpers import run_cli
from test_backup import lose_everything, populate

from mini_perpetuity import doctor
from mini_perpetuity.admin import init_instance, set_approver_password
from mini_perpetuity.backup import BackupError, setup_backup
from mini_perpetuity.config import Config, load_config
from mini_perpetuity.doctor import Check, exit_code, format_report, run_doctor

Make = Callable[..., Config]
Configure = Callable[..., tuple[Config, Path]]


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def test_a_healthy_instance_reports_ok(make_instance: Make, configure_backup: Configure) -> None:
    cfg, _ = configure_backup(make_instance())
    from mini_perpetuity.backup import make_backup

    make_backup(cfg)
    checks = by_name(run_doctor(cfg))
    assert {c.status for c in checks.values()} == {"ok"}, format_report(list(checks.values()))
    assert {"database", "schema", "approver", "pg_dump", "last backup"} <= set(checks)
    assert exit_code(list(checks.values())) == 0


def test_missing_approver_and_backups_are_warnings_with_a_fix(make_instance: Make) -> None:
    cfg = make_instance(approver=False)
    checks = by_name(run_doctor(cfg))
    assert checks["approver"].status == "warn" and "set-approver" in checks["approver"].fix
    assert checks["backups"].status == "warn" and "backup-setup" in checks["backups"].fix
    assert exit_code(list(checks.values())) == 0  # warnings do not fail the exit code


def test_no_backup_yet_and_stale_backup(make_instance: Make, configure_backup: Configure) -> None:
    from mini_perpetuity.backup import make_backup

    cfg, _ = configure_backup(make_instance())
    assert cfg.backup_dir is not None
    cfg.backup_dir.mkdir(mode=0o700)
    assert by_name(run_doctor(cfg))["last backup"].status == "warn"
    made = make_backup(cfg)
    old = time.time() - 8 * 86400
    os.utime(made.path, (old, old))
    stale = by_name(run_doctor(cfg))["last backup"]
    assert stale.status == "warn" and "8.0 days" in stale.detail and stale.fix == "mp backup"


def test_another_instances_backup_does_not_count(
    make_instance: Make, configure_backup: Configure
) -> None:
    cfg, _ = configure_backup(make_instance())
    assert cfg.backup_dir is not None
    cfg.backup_dir.mkdir(exist_ok=True)
    (cfg.backup_dir / "mp_other_20260101T000000Z.dump.age").write_bytes(b"x")
    assert by_name(run_doctor(cfg))["last backup"].detail == "none yet"


def test_a_loose_backup_folder_is_flagged(make_instance: Make, configure_backup: Configure) -> None:
    cfg, _ = configure_backup(make_instance())
    assert cfg.backup_dir is not None
    cfg.backup_dir.mkdir(exist_ok=True)
    cfg.backup_dir.chmod(0o755)
    check = by_name(run_doctor(cfg))["backup folder"]
    assert check.status == "warn" and "chmod 700" in check.fix


def test_a_missing_backup_folder_and_old_tools_fail(
    make_instance: Make,
    configure_backup: Configure,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg, _ = configure_backup(make_instance())
    gone = by_name(run_doctor(replace(cfg, backup_dir=tmp_path / "nope")))
    assert gone["backup folder"].status == "fail" and exit_code(list(gone.values())) == 1
    monkeypatch.setattr(doctor, "tool_major", lambda binary: 12)
    old = by_name(run_doctor(cfg))["pg_dump"]
    assert old.status == "fail" and "older than the server" in old.detail


def test_an_unreachable_database_is_a_finding_not_a_crash(make_instance: Make) -> None:
    cfg = replace(make_instance(), port=1)  # nothing listens here
    checks = run_doctor(cfg)
    assert [c.status for c in checks] == ["fail", "warn"]
    assert exit_code(checks) == 1


def test_an_unapplied_migration_fails_with_the_fix(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    with psycopg.connect(admin_dsn.rsplit("/", 1)[0] + "/" + cfg.dbname, autocommit=True) as a:
        a.execute("DELETE FROM schema_migrations WHERE version = '0008_backup_read.sql'")
    schema = by_name(run_doctor(cfg))["schema"]
    assert schema.status == "fail" and schema.fix == f"mp admin migrate {cfg.instance}"


# ---- the commands ----


def test_doctor_command_exit_codes(make_instance: Make) -> None:
    cfg = make_instance()
    done = run_cli(cfg, "doctor")
    assert done.returncode == 0 and "[ok  ] database" in done.stdout
    toml = cfg.password_file.with_suffix(".toml")
    toml.write_text(toml.read_text().replace(f"port = {cfg.port}", "port = 1"))
    down = run_cli(cfg, "doctor")
    assert down.returncode == 1 and "[FAIL] database" in down.stdout


def test_the_whole_daily_flow_through_the_commands(
    make_instance: Make, admin_dsn: str, tmp_path: Path, pg_bin: Path
) -> None:
    cfg = make_instance()
    populate(cfg)
    key = tmp_path / "keys" / "mine.txt"
    setup = run_cli(
        cfg, "admin", "backup-setup", cfg.instance, "--backup-dir", str(tmp_path / "bk"),
        "--key-file", str(key), "--config-dir", str(tmp_path),
        env={"MP_ADMIN_DSN": admin_dsn},
    )  # fmt: skip
    assert setup.returncode == 0, setup.stderr
    secret = key.read_text().strip()
    assert secret.startswith("AGE-SECRET-KEY-") and secret not in setup.stdout + setup.stderr
    assert key.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "bk").stat().st_mode & 0o777 == 0o700  # created private even with umask 022
    toml = cfg.password_file.with_suffix(".toml")
    toml.write_text(toml.read_text() + f'pg_bin_dir = "{pg_bin}"\n')
    cfg = load_config({"MP_CONFIG": str(toml)})
    assert secret not in toml.read_text()  # only the public key is in the config

    made = run_cli(cfg, "backup")
    assert made.returncode == 0, made.stderr
    assert cfg.backup_dir is not None
    stored = next(cfg.backup_dir.iterdir()).as_posix()
    assert run_cli(cfg, "verify-backup", stored, "--key", str(key)).returncode == 0

    lose_everything(admin_dsn, cfg)
    fresh = load_config({"MP_CONFIG": str(init_instance(admin_dsn, cfg.instance, tmp_path / "n"))})
    set_approver_password(admin_dsn, cfg.instance, "another approver passphrase 77")
    backup_file = Path(stored)
    done = run_cli(
        fresh, "admin", "restore", cfg.instance, str(backup_file), "--key", str(key),
        "--pg-bin-dir", str(pg_bin), env={"MP_ADMIN_DSN": admin_dsn},
    )  # fmt: skip
    assert done.returncode == 0 and "restored 2 memory objects" in done.stdout, done.stderr
    assert "distinctive" in run_cli(fresh, "search", "distinctive").stdout


def test_setup_refuses_unsafe_or_repeated_use(make_instance: Make, tmp_path: Path) -> None:
    cfg = make_instance()
    toml = cfg.password_file.with_suffix(".toml")
    with pytest.raises(BackupError, match="inside the backup folder"):
        setup_backup(toml, tmp_path / "bk", tmp_path / "bk" / "key.txt")
    with pytest.raises(BackupError, match="absolute"):
        setup_backup(toml, Path("bk"), tmp_path / "key.txt")
    before = toml.read_text()
    assert not (tmp_path / "bk").exists() and toml.read_text() == before  # nothing half-done
    setup_backup(toml, tmp_path / "bk", tmp_path / "key.txt")
    once = toml.read_text()
    with pytest.raises(BackupError, match="already has backup settings"):
        setup_backup(toml, tmp_path / "bk2", tmp_path / "key2.txt")
    assert toml.read_text() == once and not (tmp_path / "key2.txt").exists()
    # an existing key file is never overwritten
    toml.write_text(before)
    with pytest.raises(BackupError, match="already exists"):
        setup_backup(toml, tmp_path / "bk", tmp_path / "key.txt")
