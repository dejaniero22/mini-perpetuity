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


def verify_instance(conn: psycopg.Connection[tuple[object, ...]], cfg: Config) -> None:
    """Refuse to run if the database says it belongs to another instance."""
    row = conn.execute("SELECT instance FROM instance_meta").fetchone()
    if row is None or row[0] != cfg.instance:
        raise ConfigError(f"database {cfg.dbname} does not belong to instance {cfg.instance!r}")
