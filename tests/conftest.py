import os
import re
import secrets
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from helpers import TEST_APPROVER_PASSWORD
from psycopg import sql

from mini_perpetuity.admin import init_instance, set_approver_password
from mini_perpetuity.config import Config, approver_name, db_name, load_config, role_name


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    dsn = os.environ.get("MP_ADMIN_DSN")
    if not dsn:
        if os.environ.get("CI"):
            pytest.fail("MP_ADMIN_DSN must be set in CI; database tests may not be skipped")
        pytest.skip("MP_ADMIN_DSN not set (needs a PostgreSQL admin connection)")
    return dsn


@pytest.fixture
def make_instance(admin_dsn: str, tmp_path: Path) -> Iterator[Callable[..., Config]]:
    created: list[str] = []

    def make(*, approver: bool = True) -> Config:
        """A fresh instance. By default its approver password is set (to the test password)."""
        name = "t" + secrets.token_hex(5)
        created.append(name)
        path = init_instance(admin_dsn, name, tmp_path)
        if approver:
            set_approver_password(admin_dsn, name, TEST_APPROVER_PASSWORD)
        return load_config({"MP_CONFIG": str(path)})

    yield make
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        for name in created:
            c.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(db_name(name))
                )
            )
            for role in (role_name(name), approver_name(name)):
                c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.fixture
def register_clients() -> Callable[..., Config]:
    """Add `clients = [...]` to an instance's config file and reload it."""

    def register(cfg: Config, *names: str) -> Config:
        path = cfg.password_file.with_suffix(".toml")
        with path.open("a") as f:  # appending keeps the file's 0600 mode
            f.write("clients = [" + ", ".join(f'"{n}"' for n in names) + "]\n")
        return load_config({"MP_CONFIG": str(path)})

    return register


@pytest.fixture(scope="session")
def pg_bin(admin_dsn: str) -> Path:
    """Folder with pg_dump and pg_restore at least as new as the server (MP_PG_BIN_DIR or PATH).

    Backup tests need them. Locally a missing or too-old client skips; in CI it must fail.
    """
    configured = os.environ.get("MP_PG_BIN_DIR")
    dump = str(Path(configured) / "pg_dump") if configured else shutil.which("pg_dump")
    problem = None
    if not dump:
        problem = "pg_dump not found (set MP_PG_BIN_DIR to a PostgreSQL client bin folder)"
    else:
        out = subprocess.run([dump, "--version"], capture_output=True, text=True).stdout
        found = re.search(r"(\d+)", out.split(")", 1)[-1])
        with psycopg.connect(admin_dsn) as c:
            row = c.execute("SHOW server_version_num").fetchone()
        server = int(str(row[0])) // 10000 if row else 0
        if not found or int(found.group(1)) < server:
            problem = f"pg_dump is older than the server (version {server}): {out.strip()!r}"
    if problem:
        if os.environ.get("CI"):
            pytest.fail(f"backup tests need PostgreSQL client tools in CI: {problem}")
        pytest.skip(problem)
    assert dump is not None
    return Path(dump).parent


@pytest.fixture
def configure_backup(tmp_path: Path, pg_bin: Path) -> Callable[..., tuple[Config, Path]]:
    """Give an instance a backup folder and an age key. Returns (new Config, key file path)."""
    from pyrage import x25519

    def configure(cfg: Config, *extra_recipients: str) -> tuple[Config, Path]:
        ident = x25519.Identity.generate()
        key = tmp_path / f"{cfg.instance}-key.txt"
        key.write_text(str(ident) + "\n")
        key.chmod(0o600)
        recipients = [str(ident.to_public()), *extra_recipients]
        toml = cfg.password_file.with_suffix(".toml")
        with toml.open("a") as f:
            f.write(f'backup_dir = "{tmp_path / "backups"}"\n')
            f.write(f'pg_bin_dir = "{pg_bin}"\n')
            f.write("backup_recipients = [" + ", ".join(f'"{r}"' for r in recipients) + "]\n")
        return load_config({"MP_CONFIG": str(toml)}), key

    return configure
