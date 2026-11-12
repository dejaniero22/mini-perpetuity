import secrets
from dataclasses import dataclass

import psycopg

from mini_perpetuity.config import Config, ConfigError


def app_connect(cfg: Config) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.dbname,
        user=cfg.user,
        password=cfg.password(),
        connect_timeout=5,
    )


def approver_connect(cfg: Config, password: str) -> psycopg.Connection[tuple[object, ...]]:
    """Connect as the approver: the only role that can make memory trusted. The password is
    typed by the human and never stored, so it is always passed in, never read from a file."""
    return psycopg.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.dbname,
        user=cfg.approver,
        password=password,
        connect_timeout=5,
    )


def approver_is_set(conn: psycopg.Connection[tuple[object, ...]], cfg: Config) -> bool:
    """True once `mp admin set-approver` has given the approver role a password (it can log in)."""
    row = conn.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (cfg.approver,))
    found = row.fetchone()
    return found is not None and found[0] is True


def verify_instance(conn: psycopg.Connection[tuple[object, ...]], cfg: Config) -> None:
    """Refuse to run if the database says it belongs to another instance."""
    row = conn.execute("SELECT instance FROM instance_meta").fetchone()
    if row is None or row[0] != cfg.instance:
        raise ConfigError(f"database {cfg.dbname} does not belong to instance {cfg.instance!r}")


@dataclass(frozen=True)
class LoginCheck:
    state: str  # "enforced", "accepted" (a wrong password got in) or "unknown"
    detail: str = ""


def hba_fix(role: str) -> str:
    """What to tell the owner when PostgreSQL does not check a role's password."""
    return (
        f"PostgreSQL let {role} in with a WRONG password, so its login rule is 'trust' and it is "
        "not checking passwords for this role. Fix: in psql as the admin run `SHOW hba_file;`, "
        "open that file, add these two lines ABOVE every other 'host' line, save, then reload "
        "PostgreSQL (`SELECT pg_reload_conf();`):\n"
        "  host  all  /^mp_.*_(app|approver)$  127.0.0.1/32  scram-sha-256\n"
        "  host  all  /^mp_.*_(app|approver)$  ::1/128       scram-sha-256"
    )


def check_password_enforced(host: str, port: int, dbname: str, role: str) -> LoginCheck:
    """Try to log in as `role` with a random wrong password. If that works, PostgreSQL is set to
    'trust' for this role and the typed approver password protects nothing. The server logs one
    failed login each time this runs."""
    try:
        psycopg.connect(
            host=host, port=port, dbname=dbname, user=role,
            password=secrets.token_urlsafe(24), connect_timeout=5,
        ).close()  # fmt: skip
    except psycopg.OperationalError as e:
        # psycopg gives no error code for a failed login, so match the server's own message. A
        # server set to another language gives "unknown", which doctor shows as a warning.
        if "password authentication failed" in str(e):
            return LoginCheck("enforced")
        return LoginCheck("unknown", (str(e).strip().splitlines() or ["login failed"])[0])
    return LoginCheck("accepted")
