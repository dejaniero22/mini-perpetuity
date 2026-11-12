"""Admin-only instance creation. Run with an admin DSN (env MP_ADMIN_DSN), never the app role."""

from __future__ import annotations

import contextlib
import os
import secrets
from importlib import resources
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from mini_perpetuity.config import ConfigError, approver_name, db_name, role_name, validate_name


class InitError(Exception):
    pass


def default_config_dir() -> Path:
    return Path.home() / ".config" / "mini-perpetuity"


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _write_files(
    toml_path: Path, pw_path: Path, name: str, host: str, port: int, password: str
) -> None:
    _write_private(pw_path, password + "\n")
    _write_private(
        toml_path,
        f'instance = "{name}"\nhost = "{host}"\nport = {port}\n'
        f'password_file = "{pw_path}"\ndata_policy = "synthetic_only"\n',
    )


def _migrations() -> list[tuple[str, str]]:
    d = resources.files("mini_perpetuity") / "migrations"
    return sorted((p.name, p.read_text()) for p in d.iterdir() if p.name.endswith(".sql"))


def apply_migrations(
    c: psycopg.Connection[tuple[object, ...]], role: str, approver: str
) -> list[str]:
    """Apply unrecorded migrations, one transaction each.

    In the SQL, {app_role} and {approver_role} become identifiers and {approver_literal} a string.
    """
    c.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, body in _migrations():
        if version in done:
            continue
        with c.transaction():
            body = body.replace("{app_role}", sql.Identifier(role).as_string(c))
            body = body.replace("{approver_role}", sql.Identifier(approver).as_string(c))
            body = body.replace("{approver_literal}", sql.Literal(approver).as_string(c))
            c.execute(body)
            c.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        applied.append(version)
    return applied


def migrate_instance(admin_dsn: str, name: str) -> list[str]:
    """Apply pending migrations to an existing instance; return the versions applied."""
    try:
        validate_name(name)
    except ConfigError as e:
        raise InitError(str(e)) from e
    with psycopg.connect(make_conninfo(admin_dsn, dbname=db_name(name)), autocommit=True) as c:
        row = c.execute("SELECT instance FROM instance_meta").fetchone()
        if row is None or row[0] != name:
            raise InitError(f"database {db_name(name)} does not belong to instance {name!r}")
        return apply_migrations(c, role_name(name), approver_name(name))


def _drop(admin_dsn: str, name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name(name)))
        )
        for role in (role_name(name), approver_name(name)):
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def init_instance(admin_dsn: str, name: str, config_dir: Path) -> Path:
    """Create role + db, revoke PUBLIC, migrate, write 0600 config; return config path."""
    try:
        validate_name(name)
    except ConfigError as e:
        raise InitError(str(e)) from e
    db, role = db_name(name), role_name(name)
    toml_path, pw_path = config_dir / f"{name}.toml", config_dir / f"{name}.pw"
    if toml_path.exists() or pw_path.exists():
        raise InitError(f"{toml_path} or {pw_path} already exists; refusing to overwrite")

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        taken = admin.execute(
            "SELECT (SELECT count(*) FROM pg_database WHERE datname = %s) "
            "+ (SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s))",
            (db, role, approver_name(name)),
        ).fetchone()
        if taken is None or taken[0]:
            raise InitError(f"database {db} or role {role} already exists; refusing to reuse")
        info = admin.info
        host, port = info.host, info.port

    password = secrets.token_urlsafe(32)
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ident_db, ident_role = sql.Identifier(db), sql.Identifier(role)
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(ident_role, sql.Literal(password))
            )
            admin.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(ident_db)
            )
            admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(ident_db))
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(ident_db, ident_role)
            )
        with psycopg.connect(make_conninfo(admin_dsn, dbname=db), autocommit=True) as c:
            c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident_role))
            c.execute("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
            apply_migrations(c, role, approver_name(name))
            c.execute("INSERT INTO instance_meta (instance) VALUES (%s)", (name,))
            c.execute(
                sql.SQL("GRANT SELECT ON instance_meta, schema_migrations TO {}").format(ident_role)
            )
        _write_files(toml_path, pw_path, name, host, port, password)
    except BaseException:
        with contextlib.suppress(Exception):
            _drop(admin_dsn, name)
        for p in (toml_path, pw_path):
            p.unlink(missing_ok=True)
        raise
    return toml_path


MIN_APPROVER_PASSWORD = 16


def check_approver_password(password: str) -> None:
    """Refuse weak approver passwords: online guessing is the main attack on a typed secret."""
    if len(password) < MIN_APPROVER_PASSWORD:
        raise InitError(
            f"the approver password must be at least {MIN_APPROVER_PASSWORD} characters "
            "(a passphrase of four or five random words is easy to type and strong)"
        )
    if len(set(password)) < 6:
        raise InitError("the approver password is too repetitive")


def set_approver_password(admin_dsn: str, name: str, password: str) -> None:
    """Give an instance's approver role its password (and let it log in).

    The password is hashed here (SCRAM-SHA-256) and only the hash is sent, so it never appears in
    a SQL statement or a server log. It is not stored anywhere by this tool.
    """
    try:
        validate_name(name)
    except ConfigError as e:
        raise InitError(str(e)) from e
    check_approver_password(password)
    role = approver_name(name)
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        found = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if found is None:
            raise InitError(
                f"instance {name!r} has no approver role; run `mp admin migrate {name}` first"
            )
        verifier = admin.pgconn.encrypt_password(
            password.encode(), role.encode(), b"scram-sha-256"
        ).decode()
        admin.execute(
            sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(verifier)
            )
        )
