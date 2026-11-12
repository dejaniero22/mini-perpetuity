"""MB18 spike: can least-privilege roles do the admin tasks the web UI would offer?

Run against any PostgreSQL where you hold a superuser login (never your real data):

    MP_ADMIN_DSN='postgresql://you@localhost/postgres' uv run python spikes/mb18_roles.py

It creates two throwaway roles and one throwaway instance, tries the tasks, prints one line per
step, and removes everything. No secret is printed or stored.
"""

import contextlib
import os
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from mini_perpetuity.admin import init_instance, migrate_instance, set_approver_password
from mini_perpetuity.backup import pg_tool
from mini_perpetuity.config import approver_name, db_name, load_config, role_name
from mini_perpetuity.db import app_connect
from mini_perpetuity.memory import add_object


@dataclass(frozen=True)
class Step:
    name: str
    ok: bool
    detail: str


def _try(name: str, fn: Callable[[], str]) -> Step:
    try:
        return Step(name, True, fn())
    except Exception as e:  # noqa: BLE001 - the spike reports any failure as a finding
        first = (str(e).strip().splitlines() or [type(e).__name__])[0]
        return Step(name, False, f"{type(e).__name__}: {first}")


def _role(admin: psycopg.Connection[tuple[object, ...]], name: str, attrs: str, pw: str) -> None:
    admin.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} " + attrs).format(
            sql.Identifier(name), sql.Literal(pw)
        )
    )


def run(admin_dsn: str, pg_bin_dir: Path | None = None) -> list[Step]:
    tag = secrets.token_hex(4)
    setup, reader, inst = f"mp_spike_setup_{tag}", f"mp_spike_read_{tag}", f"spk{tag}"
    pw_setup, pw_reader = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    steps: list[Step] = []
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        _role(admin, setup, "NOSUPERUSER CREATEDB CREATEROLE NOINHERIT", pw_setup)
        _role(admin, reader, "NOSUPERUSER NOCREATEDB NOCREATEROLE", pw_reader)
        admin.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(sql.Identifier(reader)))
    setup_dsn = make_conninfo(admin_dsn, user=setup, password=pw_setup)
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / "cfg"
        try:
            steps.append(
                _try(
                    "setup role: init-instance",
                    lambda: str(init_instance(setup_dsn, inst, config_dir)),
                )
            )
            if steps[-1].ok:
                steps.append(
                    _try(
                        "setup role: migrate",
                        lambda: str(migrate_instance(setup_dsn, inst) or "up to date"),
                    )
                )
                steps.append(
                    _try(
                        "setup role: set approver password",
                        lambda: _set_approver(setup_dsn, inst),
                    )
                )
                steps.append(
                    _try("instance works for the everyday role", lambda: _use(config_dir, inst))
                )
                steps.append(
                    _try(
                        "setup role: let the read-only role connect to this instance",
                        lambda: _grant_connect(setup_dsn, inst, reader),
                    )
                )
                steps.append(
                    _try(
                        "read-only role: pg_dump",
                        lambda: _dump(admin_dsn, reader, pw_reader, inst, pg_bin_dir),
                    )
                )
                steps.append(
                    _try(
                        "read-only role: cannot write",
                        lambda: _cannot_write(admin_dsn, reader, pw_reader, inst),
                    )
                )
                steps.append(_try("setup role is not superuser", lambda: _not_super(setup_dsn)))
                steps.append(
                    _try(
                        "setup role cannot change a role it did not create",
                        lambda: _cannot_touch_others(admin_dsn, setup_dsn, tag),
                    )
                )
                steps.append(
                    _try(
                        "FINDING: the setup role owns the tables (can disable guards)",
                        lambda: _owner_can_tamper(setup_dsn, inst),
                    )
                )
        finally:
            steps.append(_try("clean-up", lambda: _cleanup(admin_dsn, inst, setup, reader)))
    return steps


def _set_approver(dsn: str, inst: str) -> str:
    set_approver_password(dsn, inst, secrets.token_urlsafe(20))
    return "ok"


def _grant_connect(dsn: str, inst: str, role: str) -> str:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db_name(inst)), sql.Identifier(role)
            )
        )
    return "ok (instances revoke CONNECT from everyone, so this is explicit per instance)"


def _use(config_dir: Path, inst: str) -> str:
    cfg = load_config({"MP_CONFIG": str(config_dir / f"{inst}.toml")})
    with app_connect(cfg) as c:
        oid = add_object(
            c, cfg, epistemic_type="human_decision", content="spike", agent="human:spike",
            activity="spike", status="proposed",
        )  # fmt: skip
    return f"added pending object {oid}"


def _dump(admin_dsn: str, user: str, pw: str, inst: str, bin_dir: Path | None) -> str:
    info = psycopg.conninfo.conninfo_to_dict(admin_dsn)
    with tempfile.TemporaryDirectory() as tmp:
        passfile = Path(tmp) / "pgpass"
        passfile.write_text(f"*:*:{db_name(inst)}:{user}:{pw}\n")
        passfile.chmod(0o600)
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                pg_tool(bin_dir, "pg_dump"), "-w", "-h", str(info.get("host", "localhost")),
                "-p", str(info.get("port", 5432)), "-U", user, "-d", db_name(inst), "-Fc",
            ],
            capture_output=True, env={"PATH": os.environ["PATH"], "PGPASSFILE": str(passfile)},
            check=False,
        )  # fmt: skip
    if done.returncode != 0 or not done.stdout.startswith(b"PGDMP"):
        raise RuntimeError(done.stderr.decode(errors="replace").strip().splitlines()[0])
    return f"{len(done.stdout)} bytes"


def _cannot_write(admin_dsn: str, user: str, pw: str, inst: str) -> str:
    dsn = make_conninfo(admin_dsn, user=user, password=pw, dbname=db_name(inst))
    with psycopg.connect(dsn, autocommit=True) as c:
        try:
            c.execute("DELETE FROM events")
        except psycopg.errors.InsufficientPrivilege:
            return "DELETE refused, as intended"
    raise RuntimeError("the read-only role could write")


def _not_super(dsn: str) -> str:
    with psycopg.connect(dsn) as c:
        row = c.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    if row is None or row[0]:
        raise RuntimeError("the setup role is a superuser")
    return "confirmed"


def _cannot_touch_others(admin_dsn: str, setup_dsn: str, tag: str) -> str:
    other = f"mp_spike_other_{tag}"
    with psycopg.connect(admin_dsn, autocommit=True) as a:
        _role(a, other, "NOSUPERUSER", secrets.token_urlsafe(16))
    try:
        with psycopg.connect(setup_dsn, autocommit=True) as c:
            try:
                c.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(other), sql.Literal(secrets.token_urlsafe(16))
                    )
                )
            except psycopg.errors.InsufficientPrivilege:
                return "refused, as intended"
        raise RuntimeError("the setup role changed a role it did not create")
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as a:
            a.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(other)))


def _owner_can_tamper(setup_dsn: str, inst: str) -> str:
    dsn = make_conninfo(setup_dsn, dbname=db_name(inst))
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("ALTER TABLE events DISABLE TRIGGER USER")
        c.execute("ALTER TABLE events ENABLE TRIGGER USER")  # put it back
    return "yes: so the setup role must be unlocked per action, never left in the UI process"


def _cleanup(admin_dsn: str, inst: str, *roles: str) -> str:
    with psycopg.connect(admin_dsn, autocommit=True) as a:
        a.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name(inst)))
        )
        for role in (role_name(inst), approver_name(inst), *roles):
            with contextlib.suppress(psycopg.Error):
                a.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            a.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
    return "removed"


def main() -> int:
    dsn = os.environ.get("MP_ADMIN_DSN")
    if not dsn:
        print("set MP_ADMIN_DSN to a superuser connection string", file=sys.stderr)
        return 2
    bin_dir = Path(os.environ["MP_PG_BIN_DIR"]) if "MP_PG_BIN_DIR" in os.environ else None
    results = run(dsn, bin_dir)
    for s in results:
        print(f"[{'ok  ' if s.ok else 'FAIL'}] {s.name}: {s.detail}")
    return 0 if all(s.ok for s in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
