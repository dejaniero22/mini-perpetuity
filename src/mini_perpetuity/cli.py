import argparse
import os
import sys
from pathlib import Path

import psycopg

from mini_perpetuity.admin import InitError, default_config_dir, init_instance, migrate_instance
from mini_perpetuity.config import ConfigError, load_config
from mini_perpetuity.db import app_connect, verify_instance


def _status() -> int:
    cfg = load_config()
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
    print(f"instance={cfg.instance} db={cfg.dbname} data_policy={cfg.data_policy}")
    return 0


def _admin_dsn() -> str:
    dsn = os.environ.get("MP_ADMIN_DSN")
    if not dsn:
        raise InitError("MP_ADMIN_DSN is not set")
    return dsn


def _migrate(name: str) -> int:
    applied = migrate_instance(_admin_dsn(), name)
    print(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")
    return 0


def _init(name: str, config_dir: Path) -> int:
    dsn = _admin_dsn()
    path = init_instance(dsn, name, config_dir)
    print(f"created instance {name}; config: {path}")
    print(f"use it with: MP_CONFIG={path} mp status")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mp")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="check config and database for this process's instance")
    admin = sub.add_parser("admin", help="admin-only commands").add_subparsers(
        dest="admin_cmd", required=True
    )
    init = admin.add_parser("init-instance", help="create an instance (needs MP_ADMIN_DSN)")
    init.add_argument("name")
    init.add_argument("--config-dir", type=Path, default=default_config_dir())
    admin.add_parser("migrate", help="apply pending migrations (needs MP_ADMIN_DSN)").add_argument(
        "name"
    )
    args = p.parse_args(argv)
    try:
        if args.cmd == "status":
            return _status()
        if args.admin_cmd == "migrate":
            return _migrate(args.name)
        return _init(args.name, args.config_dir)
    except (ConfigError, InitError, psycopg.Error) as e:
        print(f"mp: error: {e}", file=sys.stderr)
        return 2 if isinstance(e, ConfigError | InitError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
