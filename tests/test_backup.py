"""Backup, verify and restore against a real PostgreSQL (MP_ADMIN_DSN) and its client tools."""

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
from helpers import trusted_connect
from psycopg import sql
from pyrage import x25519

from mini_perpetuity import backup
from mini_perpetuity.admin import init_instance, set_approver_password
from mini_perpetuity.backup import BackupError, make_backup, restore_backup, verify_backup
from mini_perpetuity.config import (
    Config,
    ConfigError,
    approver_name,
    db_name,
    load_config,
    role_name,
)
from mini_perpetuity.db import app_connect
from mini_perpetuity.memory import add_object
from mini_perpetuity.search import search_memory

Make = Callable[..., Config]
Configure = Callable[..., tuple[Config, Path]]
DISTINCTIVE = "zebra-quartz-distinctive-memory"
EXPECTED_TABLES = ("events", "instance_meta", "memory_objects", "schema_migrations")


def populate(cfg: Config) -> tuple[int, int]:
    with trusted_connect(cfg) as c:
        approved = add_object(
            c, cfg, epistemic_type="human_decision", content=f"{DISTINCTIVE} approved",
            agent="human:test", activity="t", status="approved",
        )  # fmt: skip
    with app_connect(cfg) as c:
        pending = add_object(
            c, cfg, epistemic_type="human_decision", content="a pending note",
            agent="human:test", activity="t", status="proposed",
        )  # fmt: skip
    return approved, pending


def count(cfg: Config, table: str) -> int:
    with app_connect(cfg) as c:
        row = c.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    assert row is not None and isinstance(row[0], int)
    return row[0]


def need_age() -> str:
    found = shutil.which("age")
    if not found:
        if os.environ.get("CI"):
            pytest.fail("the standard `age` tool must be installed in CI")
        pytest.skip("the standard `age` tool is not installed")
    return found


# ---- making a backup ----


def test_a_backup_is_complete_private_and_encrypted(
    make_instance: Make, configure_backup: Configure
) -> None:
    cfg, key = configure_backup(make_instance())
    populate(cfg)
    made = make_backup(cfg)
    assert made.path.parent == cfg.backup_dir
    assert re.fullmatch(rf"mp_{cfg.instance}_\d{{8}}T\d{{6}}Z\.dump\.age", made.path.name)
    assert made.path.stat().st_mode & 0o777 == 0o600
    assert made.path.parent.stat().st_mode & 0o777 == 0o700
    assert [p.name for p in made.path.parent.iterdir()] == [made.path.name]  # no temp leftovers
    raw = made.path.read_bytes()
    assert raw.startswith(b"age-encryption.org/v1") and made.size == len(raw)
    assert DISTINCTIVE.encode() not in raw and b"PGDMP" not in raw[:64]  # nothing readable
    assert verify_backup(made.path, key, cfg.pg_bin_dir).tables_with_data == EXPECTED_TABLES
    with app_connect(cfg) as c:
        rows = c.execute("SELECT payload FROM events WHERE activity = 'backup.created'").fetchall()
    assert [r[0]["file"] for r in rows] == [made.path.name]  # type: ignore[index]


def test_a_backup_is_a_standard_age_file(make_instance: Make, configure_backup: Configure) -> None:
    age = need_age()
    cfg, key = configure_backup(make_instance())
    populate(cfg)
    made = make_backup(cfg)
    out = subprocess.run(
        [age, "-d", "-i", str(key), str(made.path)], capture_output=True, check=True
    )
    assert out.stdout.startswith(b"PGDMP")  # decrypted by the reference tool, no project code


def test_either_recipient_can_open_it(make_instance: Make, configure_backup: Configure) -> None:
    spare = x25519.Identity.generate()
    cfg, key = configure_backup(make_instance(), str(spare.to_public()))
    populate(cfg)
    made = make_backup(cfg)
    spare_key = key.with_name("spare.txt")
    spare_key.write_text(str(spare) + "\n")
    for k in (key, spare_key):
        assert verify_backup(made.path, k, cfg.pg_bin_dir).tables_with_data == EXPECTED_TABLES


def test_an_existing_backup_is_never_overwritten(
    make_instance: Make, configure_backup: Configure
) -> None:
    from datetime import UTC, datetime

    cfg, _ = configure_backup(make_instance())
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    first = make_backup(cfg, now=when)
    before = first.path.read_bytes()
    with pytest.raises(BackupError, match="already exists"):
        make_backup(cfg, now=when)
    assert first.path.read_bytes() == before and len(list(first.path.parent.iterdir())) == 1


def test_an_interrupted_write_leaves_nothing_behind(
    make_instance: Make, configure_backup: Configure, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = configure_backup(make_instance())

    def boom(fd: int) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disk went away"):
        make_backup(cfg)
    assert cfg.backup_dir is not None
    assert not cfg.backup_dir.exists() or list(cfg.backup_dir.iterdir()) == []


def test_the_password_never_reaches_a_command_line_or_environment(
    make_instance: Make, configure_backup: Configure, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = configure_backup(make_instance())
    password = cfg.password()
    seen: dict[str, Any] = {}
    real_run = subprocess.run

    def spy(argv: list[str], **kw: Any) -> Any:
        if Path(argv[0]).name == "pg_dump" and "-Fc" in argv:
            env = kw["env"]
            passfile = Path(env["PGPASSFILE"])
            seen.update(
                argv=" ".join(argv), env=" ".join(env.values()), passfile=passfile,
                mode=passfile.stat().st_mode & 0o777, content=passfile.read_text(),
            )  # fmt: skip
        return real_run(argv, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    make_backup(cfg)
    assert password not in seen["argv"] and password not in seen["env"]
    assert "PGPASSWORD" not in seen["env"] and seen["mode"] == 0o600
    assert password in seen["content"]  # it was in the private file, as intended
    assert not seen["passfile"].exists() and not seen["passfile"].parent.exists()  # and removed


# ---- refusals with a clear message ----


def test_backups_need_to_be_set_up(make_instance: Make) -> None:
    with pytest.raises(BackupError, match="not set up"):
        make_backup(make_instance())


def test_an_old_pg_dump_is_refused(
    make_instance: Make, configure_backup: Configure, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, _ = configure_backup(make_instance())
    monkeypatch.setattr(backup, "tool_major", lambda binary: 12)
    with pytest.raises(BackupError, match="same version as the server or newer"):
        make_backup(cfg)
    assert cfg.backup_dir is not None
    assert not cfg.backup_dir.exists() or list(cfg.backup_dir.iterdir()) == []


def test_missing_postgres_tools_are_explained(
    make_instance: Make, configure_backup: Configure, tmp_path: Path
) -> None:
    cfg, _ = configure_backup(make_instance())
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    with pytest.raises(BackupError, match="pg_dump not found"):
        make_backup(replace(cfg, pg_bin_dir=empty))


def test_wrong_key_damage_and_junk_are_refused(
    make_instance: Make, configure_backup: Configure, tmp_path: Path
) -> None:
    cfg, key = configure_backup(make_instance())
    made = make_backup(cfg)
    stranger = tmp_path / "stranger.txt"
    stranger.write_text(str(x25519.Identity.generate()) + "\n")
    no_key = tmp_path / "nokey.txt"
    no_key.write_text("not a key\n")
    raw = made.path.read_bytes()
    flipped = tmp_path / "flipped.age"
    flipped.write_bytes(raw[:-5] + bytes([raw[-5] ^ 1]) + raw[-4:])
    short = tmp_path / "short.age"
    short.write_bytes(raw[: len(raw) // 2])
    junk = tmp_path / "junk.age"
    junk.write_bytes(b"this is not an age file")
    bin_dir = cfg.pg_bin_dir
    for path, ident, message in (
        (made.path, stranger, "wrong key"),
        (flipped, key, "cannot decrypt"),
        (short, key, "cannot decrypt"),
        (junk, key, "cannot decrypt"),
        (made.path, no_key, "no age secret key"),
        (made.path, tmp_path / "missing.txt", "cannot read the key file"),
        (tmp_path / "missing.age", key, "cannot read"),
    ):
        with pytest.raises(BackupError, match=message):
            verify_backup(path, ident, bin_dir)


# ---- restoring ----


def lose_everything(admin_dsn: str, cfg: Config) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as a:
        a.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name(cfg.instance)))
        )
        for role in (role_name(cfg.instance), approver_name(cfg.instance)):
            a.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_a_backup_restores_after_a_total_loss(
    make_instance: Make, configure_backup: Configure, admin_dsn: str, tmp_path: Path
) -> None:
    cfg, key = configure_backup(make_instance())
    approved, pending = populate(cfg)
    events_at_dump = count(cfg, "events")
    made = make_backup(cfg)
    lose_everything(admin_dsn, cfg)  # the database, its roles, the config: gone
    fresh = load_config(
        {"MP_CONFIG": str(init_instance(admin_dsn, cfg.instance, tmp_path / "new"))}
    )
    set_approver_password(admin_dsn, cfg.instance, "a brand new approver passphrase 99")

    restored = restore_backup(admin_dsn, cfg.instance, made.path, key, cfg.pg_bin_dir)
    assert (restored.memory_objects, restored.events) == (2, events_at_dump)

    with app_connect(fresh) as c:
        hits = search_memory(
            c, fresh, __import__("mini_perpetuity.authz", fromlist=["Request"]).Request(
                fresh.owner, fresh.instance, "search"
            ), "distinctive",
        )  # fmt: skip
    assert [m.id for m in hits] == [approved]  # trusted memory is back and searchable
    with app_connect(fresh) as c:
        statuses = c.execute("SELECT id, status FROM memory_objects ORDER BY id").fetchall()
        newest = add_object(
            c, fresh, epistemic_type="human_decision", content="after the restore",
            agent="human:test", activity="t", status="proposed",
        )  # fmt: skip
        triggers = c.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname IN ('memory_objects_guard', "
            "'memory_objects_client_insert_guard', 'events_append_only', 'events_trusted_guard') "
            "AND tgenabled = 'O'"
        ).fetchone()
        marker = c.execute("SELECT actor FROM events WHERE activity = 'backup.restored'").fetchall()
    assert statuses == [(approved, "approved"), (pending, "proposed")]  # nothing was promoted
    assert newest == pending + 1  # new ids continue after the restored ones
    assert triggers == (4,)  # the append-only and approval guards are switched back on
    assert marker == [("admin",)]
    with app_connect(fresh) as c, pytest.raises(psycopg.errors.RestrictViolation):
        c.execute(  # the everyday role still cannot create trusted memory
            "INSERT INTO memory_objects (epistemic_type, content, valid_at, status, "
            "generated_by_activity, attributed_to_agent) "
            "VALUES ('human_decision', 'x', now(), 'approved', 't', 'human:x')"
        )


def test_restore_refuses_an_instance_that_already_holds_data(
    make_instance: Make, configure_backup: Configure, admin_dsn: str
) -> None:
    cfg, key = configure_backup(make_instance())
    populate(cfg)
    made = make_backup(cfg)
    before = (count(cfg, "memory_objects"), count(cfg, "events"))
    with pytest.raises(BackupError, match="already holds data"):
        restore_backup(admin_dsn, cfg.instance, made.path, key, cfg.pg_bin_dir)
    assert (count(cfg, "memory_objects"), count(cfg, "events")) == before


def test_restore_into_another_instance_name_fails_and_changes_nothing(
    make_instance: Make, configure_backup: Configure, admin_dsn: str
) -> None:
    cfg, key = configure_backup(make_instance())
    populate(cfg)
    made = make_backup(cfg)
    other = make_instance()  # ledger events carry their instance name, so this cannot work
    with pytest.raises(BackupError, match="pg_restore failed"):
        restore_backup(admin_dsn, other.instance, made.path, key, cfg.pg_bin_dir)
    assert (count(other, "memory_objects"), count(other, "events")) == (0, 0)  # all or nothing


def test_restore_explains_a_missing_or_misnamed_instance(
    make_instance: Make, configure_backup: Configure, admin_dsn: str
) -> None:
    cfg, key = configure_backup(make_instance())
    made = make_backup(cfg)
    with pytest.raises(BackupError, match="cannot open instance"):
        restore_backup(admin_dsn, "nosuchinstance", made.path, key, cfg.pg_bin_dir)
    with pytest.raises(ConfigError):  # a bad name is refused before anything is touched
        restore_backup(admin_dsn, "Bad_Name", made.path, key, cfg.pg_bin_dir)
