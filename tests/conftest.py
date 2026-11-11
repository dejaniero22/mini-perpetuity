import os
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from mini_perpetuity.admin import init_instance
from mini_perpetuity.config import Config, db_name, load_config, role_name


@pytest.fixture(scope="session")
def admin_dsn() -> str:
    dsn = os.environ.get("MP_ADMIN_DSN")
    if not dsn:
        if os.environ.get("CI"):
            pytest.fail("MP_ADMIN_DSN must be set in CI; database tests may not be skipped")
        pytest.skip("MP_ADMIN_DSN not set (needs a PostgreSQL admin connection)")
    return dsn


@pytest.fixture
def make_instance(admin_dsn: str, tmp_path: Path) -> Iterator[Callable[[], Config]]:
    created: list[str] = []

    def make() -> Config:
        name = "t" + secrets.token_hex(5)
        created.append(name)
        path = init_instance(admin_dsn, name, tmp_path)
        return load_config({"MP_CONFIG": str(path)})

    yield make
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        for name in created:
            c.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(db_name(name))
                )
            )
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name(name))))


@pytest.fixture
def register_clients() -> Callable[..., Config]:
    """Add `clients = [...]` to an instance's config file and reload it."""

    def register(cfg: Config, *names: str) -> Config:
        path = cfg.password_file.with_suffix(".toml")
        with path.open("a") as f:  # appending keeps the file's 0600 mode
            f.write("clients = [" + ", ".join(f'"{n}"' for n in names) + "]\n")
        return load_config({"MP_CONFIG": str(path)})

    return register
