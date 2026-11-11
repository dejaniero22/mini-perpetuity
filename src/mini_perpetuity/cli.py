import argparse
import getpass
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg

from mini_perpetuity.access import RateLimited
from mini_perpetuity.admin import InitError, default_config_dir, init_instance, migrate_instance
from mini_perpetuity.authz import Denied, Request
from mini_perpetuity.config import Config, ConfigError, load_config
from mini_perpetuity.db import app_connect, verify_instance
from mini_perpetuity.mcp_server import build_server
from mini_perpetuity.memory import HUMAN_TYPES, add_object
from mini_perpetuity.review import ReviewError, decide, list_proposals
from mini_perpetuity.search import search_memory
from mini_perpetuity.seatbelt import Rejected


def _status() -> int:
    cfg = load_config()
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
    print(f"instance={cfg.instance} db={cfg.dbname} data_policy={cfg.data_policy}")
    return 0


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe_text(text: str) -> str:
    """Stored text is untrusted: neutralize control characters (terminal escape sequences)."""
    return _CONTROL.sub("?", text)


def _search(args: argparse.Namespace) -> int:
    cfg = load_config()
    request = Request(f"human:{getpass.getuser()}", cfg.instance, "search")
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        hits = search_memory(conn, cfg, request, args.query, history=args.history, limit=args.limit)
    for m in hits:
        known = f"known {m.created_at:%Y-%m-%d}" + (
            f"..{m.expired_at:%Y-%m-%d}" if m.expired_at else ""
        )
        valid = f"valid {m.valid_at:%Y-%m-%d}.." + (
            f"{m.invalid_at:%Y-%m-%d}" if m.invalid_at else ""
        )
        print(f"#{m.id}  {m.epistemic_type}  {m.status}  {valid}  {known}")
        for line in safe_text(m.content).splitlines() or [""]:
            print(f"    {line}")
    print(f"{len(hits)} result(s)")
    return 0


def _owner_request(cfg: Config, action: str) -> Request:
    return Request(f"human:{getpass.getuser()}", cfg.instance, action)


def _review() -> int:
    cfg = load_config()
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        pending = list_proposals(conn, cfg, _owner_request(cfg, "review"))
    print(
        f"{len(pending)} pending proposal(s). Text after '|' is untrusted, written by an AI client."
    )
    for m in pending:
        source = safe_text(m.was_derived_from or "")
        print(f"\n#{m.id}  {m.epistemic_type}  from {safe_text(m.attributed_to_agent)}"
              f"  proposed {m.created_at:%Y-%m-%d %H:%M}  derived from: {source}")  # fmt: skip
        for line in safe_text(m.content).splitlines() or [""]:
            print(f"    | {line}")
    if pending:
        print("\nApprove: mp approve ID    Reject: mp reject ID --reason TEXT")
    return 0


def _decide(args: argparse.Namespace, *, approve: bool) -> int:
    cfg = load_config()
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        m = decide(
            conn, cfg, _owner_request(cfg, "approve" if approve else "reject"), args.id,
            approve=approve, reason=None if approve else args.reason,
        )  # fmt: skip
    verb = "approved" if approve else "rejected"
    print(f"{verb} #{m.id} ({m.epistemic_type} from {safe_text(m.attributed_to_agent)})")
    return 0


def _serve(args: argparse.Namespace) -> int:
    cfg = load_config({"MP_CONFIG": args.config} if args.config else None)
    if args.client not in cfg.clients:
        raise ConfigError(
            f"client {args.client!r} is not registered for instance {cfg.instance!r}; "
            f"add it to `clients = [...]` in {args.config or 'the config file'}"
        )
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)  # fail now, not on the first tool call
    build_server(cfg, args.client).run()  # stdio; stdout carries the protocol
    return 0


def _add(args: argparse.Namespace) -> int:
    content = sys.stdin.read().strip() if args.content == "-" else args.content
    valid_at = datetime.fromisoformat(args.valid_at) if args.valid_at else None
    if valid_at is not None and valid_at.tzinfo is None:
        raise ValueError("--valid-at needs a timezone offset, e.g. 2026-01-31T00:00:00+00:00")
    if args.type == "source_record" and not args.source:
        raise ValueError("--source is required for a source_record (a pointer, not a copy)")
    cfg = load_config()
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        oid = add_object(
            conn,
            cfg,
            epistemic_type=args.type,
            content=content,
            agent=f"human:{getpass.getuser()}",
            activity="mp add",
            status="approved",
            valid_at=valid_at,
            derived_from=args.source,
            sensitivity=args.sensitivity,
        )
    print(f"added memory object {oid}")
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
    add = sub.add_parser("add", help="add a human decision or source record")
    add.add_argument("--type", choices=HUMAN_TYPES, required=True)
    add.add_argument("--content", required=True, help="text, or - to read stdin")
    add.add_argument("--source", help="pointer to the source (required for source_record)")
    add.add_argument("--valid-at", help="ISO 8601 with offset; default now")
    add.add_argument("--sensitivity", choices=("normal", "sensitive"), default="normal")
    search = sub.add_parser("search", help="full-text search over approved memory")
    search.add_argument("query", help='words; "quoted phrase", -excluded, or are supported')
    search.add_argument("--history", action="store_true", help="include superseded versions")
    search.add_argument("--limit", type=int, default=10, help="1 to 50 (default 10)")
    serve = sub.add_parser("serve", help="run the read-only MCP server (stdio) for one client")
    serve.add_argument(
        "--client", required=True, help="registered client name, e.g. claude-desktop"
    )
    serve.add_argument("--config", help="instance config file (default: $MP_CONFIG)")
    sub.add_parser("review", help="list AI proposals waiting for your decision")
    approve = sub.add_parser("approve", help="approve a pending proposal (human only)")
    approve.add_argument("id", type=int)
    reject = sub.add_parser("reject", help="reject a pending proposal (human only)")
    reject.add_argument("id", type=int)
    reject.add_argument("--reason", required=True, help="why (recorded in the ledger)")
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
        if args.cmd == "add":
            return _add(args)
        if args.cmd == "search":
            return _search(args)
        if args.cmd == "serve":
            return _serve(args)
        if args.cmd == "review":
            return _review()
        if args.cmd in ("approve", "reject"):
            return _decide(args, approve=args.cmd == "approve")
        if args.admin_cmd == "migrate":
            return _migrate(args.name)
        return _init(args.name, args.config_dir)
    except (
        ConfigError,
        InitError,
        Denied,
        RateLimited,
        Rejected,
        ReviewError,
        ValueError,
        psycopg.Error,
    ) as e:
        print(f"mp: error: {e}", file=sys.stderr)
        return 1 if isinstance(e, psycopg.Error) else 2


if __name__ == "__main__":
    raise SystemExit(main())
