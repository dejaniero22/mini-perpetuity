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
