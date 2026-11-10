"""Isolation tests against a real PostgreSQL (MP_ADMIN_DSN). CI runs these on PostgreSQL 18."""

import itertools
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from mini_perpetuity import admin
from mini_perpetuity.admin import InitError, init_instance
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect

Make = Callable[[], Config]


def connect_as(cfg: Config, target: Config) -> None:
    psycopg.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=target.dbname,
        user=cfg.user,
        password=cfg.password(),
        connect_timeout=5,
    ).close()


def test_crossover_all_pairs(make_instance: Make) -> None:
    cfgs = [make_instance() for _ in range(3)]
    for c in cfgs:
        app_connect(c).close()
    for a, b in itertools.permutations(cfgs, 2):
        with pytest.raises(psycopg.OperationalError):
            connect_as(a, b)


def test_public_has_no_connect_or_temp(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    with psycopg.connect(admin_dsn) as c:
        row = c.execute(
            "SELECT d.datacl IS NOT NULL, count(a.grantee) "
            "FROM pg_database d LEFT JOIN LATERAL aclexplode(d.datacl) a ON a.grantee = 0 "
            "WHERE d.datname = %s GROUP BY d.datacl",
            (cfg.dbname,),
        ).fetchone()
    assert row == (True, 0)


def test_function_execute_not_granted_to_public(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    dsn = psycopg.conninfo.make_conninfo(admin_dsn, dbname=cfg.dbname)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE FUNCTION probe() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        row = c.execute(
            "SELECT p.proacl IS NOT NULL, count(a.grantee) FROM pg_proc p "
            "LEFT JOIN LATERAL aclexplode(p.proacl) a ON a.grantee = 0 "
            "WHERE p.proname = 'probe' GROUP BY p.proacl"
        ).fetchone()
    assert row == (True, 0)


def test_app_role_is_unprivileged(make_instance: Make) -> None:
    cfg = make_instance()
    with app_connect(cfg) as c:
        row = c.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert row == (False,) * 5
        for stmt in (
            "CREATE TABLE x (a int)",
            "INSERT INTO schema_migrations (version) VALUES ('x')",
            "UPDATE instance_meta SET instance = 'x'",
            "CREATE ROLE evil",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()
    with app_connect(cfg) as c:
        c.autocommit = True
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("CREATE DATABASE evil")


def test_status_cli(make_instance: Make, admin_dsn: str) -> None:
    cfg = make_instance()
    path = cfg.password_file.with_suffix(".toml")
    env = {"MP_CONFIG": str(path), "PATH": "/usr/bin:/bin"}
    r = subprocess.run(
        [sys.executable, "-m", "mini_perpetuity", "status"], env=env, capture_output=True, text=True
    )
    assert r.returncode == 0 and f"instance={cfg.instance}" in r.stdout
    # Database claims to belong to another instance -> refuse.
    dsn = psycopg.conninfo.make_conninfo(admin_dsn, dbname=cfg.dbname)
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("UPDATE instance_meta SET instance = 'zzz'")
    r = subprocess.run(
        [sys.executable, "-m", "mini_perpetuity", "status"], env=env, capture_output=True, text=True
    )
    assert r.returncode == 2 and "does not belong" in r.stderr


def test_init_refuses_existing(make_instance: Make, admin_dsn: str, tmp_path: Path) -> None:
    cfg = make_instance()
    before = cfg.password_file.read_text()
    other_dir = tmp_path / "other"  # different dir: only the database/role check can refuse
    with pytest.raises(InitError, match="already exists"):
        init_instance(admin_dsn, cfg.instance, other_dir)
    with pytest.raises(InitError, match="refusing to overwrite"):
        init_instance(admin_dsn, cfg.instance, cfg.password_file.parent)
    assert cfg.password_file.read_text() == before
    app_connect(cfg).close()  # still works with the original password


@pytest.mark.parametrize("name", ["Bad", "a_b", ""])
def test_init_rejects_bad_name(admin_dsn: str, tmp_path: Path, name: str) -> None:
    with pytest.raises(InitError):
        init_instance(admin_dsn, name, tmp_path)


def test_init_cleans_up_on_failure(
    admin_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(admin, "_write_files", boom)
    with pytest.raises(RuntimeError):
        init_instance(admin_dsn, "tfail1", tmp_path)
    with psycopg.connect(admin_dsn) as c:
        row = c.execute(
            "SELECT (SELECT count(*) FROM pg_database WHERE datname = 'mp_tfail1') "
            "+ (SELECT count(*) FROM pg_roles WHERE rolname = 'mp_tfail1_app')"
        ).fetchone()
    assert row == (0,)
