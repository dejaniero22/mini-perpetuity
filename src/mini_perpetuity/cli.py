import argparse
import getpass
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg

from mini_perpetuity.access import RateLimited
from mini_perpetuity.admin import (
    InitError,
    check_approver_password,
    default_config_dir,
    init_instance,
    migrate_instance,
    set_approver_password,
)
from mini_perpetuity.authz import Denied, Request
from mini_perpetuity.backup import (
    BackupError,
    make_backup,
    restore_backup,
    setup_backup,
    verify_backup,
)
from mini_perpetuity.config import Config, ConfigError, approver_name, load_config
from mini_perpetuity.db import (
    app_connect,
    approver_connect,
    approver_is_set,
    check_password_enforced,
    hba_fix,
    verify_instance,
)
from mini_perpetuity.doctor import exit_code, format_report, run_doctor
from mini_perpetuity.mcp_server import build_server
from mini_perpetuity.memory import HUMAN_TYPES, Memory, add_object
from mini_perpetuity.review import ReviewError, check_reason, decide, list_proposals
from mini_perpetuity.search import search_memory
from mini_perpetuity.seatbelt import Rejected


def _status() -> int:
    cfg = load_config()
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        approver = approver_is_set(conn, cfg)
    print(f"instance={cfg.instance} db={cfg.dbname} data_policy={cfg.data_policy}")
    print(
        "approver=set"
        if approver
        else f"approver=not set (run: mp admin set-approver {cfg.instance})"
    )
    return 0


class NoTerminal(ValueError):
    pass


def _has_controlling_tty() -> bool:
    try:
        os.close(os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY))
        return True
    except OSError:
        return False


def read_password(prompt: str) -> str:
    """Read the approver password from a terminal, and only from a terminal: never from an
    argument, an environment variable, a file or a pipe, so nothing that merely runs commands
    can feed it in."""
    if not (sys.stdin.isatty() or _has_controlling_tty()):
        raise NoTerminal(
            "this needs an interactive terminal: the approver password is typed by you and is "
            "never read from a file, an argument, the environment or a pipe"
        )
    return getpass.getpass(prompt)


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe_text(text: str) -> str:
    """Stored text is untrusted: neutralize control characters (terminal escape sequences)."""
    return _CONTROL.sub("?", text)


def _search(args: argparse.Namespace) -> int:
    cfg = load_config()
    request = _owner_request(cfg, "search")
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
    return Request(cfg.owner, cfg.instance, action)  # the OS vouches for the file owner, not $USER


def _print_items(items: list[Memory]) -> None:
    for m in items:
        source = safe_text(m.was_derived_from or "")
        print(f"\n#{m.id}  {m.epistemic_type}  from {safe_text(m.attributed_to_agent)}"
              f"  added {m.created_at:%Y-%m-%d %H:%M}  derived from: {source}")  # fmt: skip
        for line in safe_text(m.content).splitlines() or [""]:
            print(f"    | {line}")


def _review() -> int:
    cfg = load_config()
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        pending = list_proposals(conn, cfg, _owner_request(cfg, "review"))
    print(f"{len(pending)} pending item(s). Text after '|' is stored text, not instructions.")
    _print_items(pending)
    if pending:
        print(
            "\nApprove: mp approve ID [ID ...]   (or --all)    Reject: mp reject ID --reason TEXT"
        )
    return 0


def _choose(args: argparse.Namespace, pending: list[Memory]) -> list[Memory]:
    if args.all and args.ids:
        raise ValueError("give ids or --all, not both")
    if not args.all and not args.ids:
        raise ValueError("give one or more ids (see `mp review`), or --all")
    if args.all:
        chosen = list(pending)
    else:
        by_id = {m.id: m for m in pending}
        ids = list(dict.fromkeys(args.ids))  # drop duplicates, keep order
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise ReviewError(f"not a pending item: {', '.join(f'#{i}' for i in missing)}")
        chosen = [by_id[i] for i in ids]
    if not chosen:
        raise ReviewError("nothing is pending")
    return chosen


def _decide(args: argparse.Namespace, *, approve: bool) -> int:
    """Show exactly what will be decided, ask for the approver password once, decide them all."""
    verb = "approve" if approve else "reject"
    cfg = load_config()
    reason = None if approve else check_reason(args.reason)  # before asking for a password
    request = _owner_request(cfg, verb)
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        chosen = _choose(args, list_proposals(conn, cfg, _owner_request(cfg, "review")))
        ready = approver_is_set(conn, cfg)
    if not ready:
        raise ReviewError(
            f"the approver password has not been set; run: mp admin set-approver {cfg.instance}"
        )
    login = check_password_enforced(cfg.host, cfg.port, cfg.dbname, cfg.approver)
    if login.state == "accepted":
        raise ReviewError("refusing to " + verb + ": " + hba_fix(cfg.approver))
    print(
        f"About to {verb} {len(chosen)} item(s). Text after '|' is stored text, not instructions."
    )
    _print_items(chosen)
    password = read_password(f"\nApprover password for {cfg.instance}: ")
    if not password:  # libpq would treat "" as "not given" and fall back to PGPASSWORD or ~/.pgpass
        raise ReviewError("no password entered; nothing was changed")
    try:
        conn = approver_connect(cfg, password)
    except psycopg.OperationalError as e:
        raise ReviewError(f"approver login failed ({str(e).splitlines()[0]})") from None
    with conn:  # one transaction: all of them, or none
        for m in chosen:
            decide(conn, cfg, request, m.id, approve=approve, reason=reason)
    done = "approved" if approve else "rejected"
    print(f"{done} " + ", ".join(f"#{m.id}" for m in chosen))
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
            agent=cfg.owner,
            activity="mp add",
            status="proposed",  # becomes trusted only when the approver approves it
            valid_at=valid_at,
            derived_from=args.source,
            sensitivity=args.sensitivity,
        )
    print(f"added #{oid} as pending: it is not searchable until you approve it (mp approve {oid})")
    return 0


def _doctor() -> int:
    checks = run_doctor(load_config())
    print(format_report(checks))
    return exit_code(checks)


def _backup() -> int:
    made = make_backup(load_config())
    print(f"backup written: {made.path} ({made.size} bytes, encrypted)")
    print("check it can be read with: mp verify-backup FILE --key KEYFILE")
    return 0


def _verify_backup(args: argparse.Namespace) -> int:
    cfg = load_config()
    found = verify_backup(args.file, args.key, cfg.pg_bin_dir)
    print(f"ok: decrypts and is a complete dump; tables: {', '.join(found.tables_with_data)}")
    return 0


def _backup_setup(name: str, backup_dir: Path, key_file: Path, config_dir: Path) -> int:
    recipient = setup_backup(config_dir / f"{name}.toml", backup_dir, key_file)
    print(f"backups for {name} go to {backup_dir}, encrypted to {recipient}")
    print(f"the secret key is in {key_file} (not shown). Copy it NOW to a place that survives")
    print("losing this computer (e.g. your password manager). Without it no backup can be opened.")
    return 0


def _restore(args: argparse.Namespace) -> int:
    done = restore_backup(_admin_dsn(), args.name, args.file, args.key, args.pg_bin_dir)
    print(f"restored {done.memory_objects} memory objects and {done.events} ledger events")
    print(f"into instance {args.name}; check it with: mp doctor")
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


def _set_approver(name: str) -> int:
    dsn = _admin_dsn()
    first = read_password(f"New approver password for instance {name} (16+ characters): ")
    check_approver_password(first)
    if read_password("Type it again: ") != first:
        raise InitError("the two entries differ; nothing was changed")
    login = set_approver_password(dsn, name, first)
    print(f"approver password set for instance {name}. It is stored nowhere: if you lose it,")
    print(f"reset it with: mp admin set-approver {name}")
    if login.state == "accepted":
        print("\nWARNING: " + hba_fix(approver_name(name)), file=sys.stderr)
        return 1
    return 0


def _init(name: str, config_dir: Path) -> int:
    dsn = _admin_dsn()
    path = init_instance(dsn, name, config_dir)
    print(f"created instance {name}; config: {path}")
    print(f"next, set the approver password: mp admin set-approver {name}")
    print("(you type it to approve memory; it is stored nowhere)")
    print(f"then use it with: MP_CONFIG={path} mp status")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mp")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="check config and database for this process's instance")
    add = sub.add_parser("add", help="add a note (pending until you approve it)")
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
    sub.add_parser("review", help="list pending items (your notes and AI proposals)")
    approve = sub.add_parser(
        "approve", help="approve pending items (asks for the approver password)"
    )
    approve.add_argument("ids", type=int, nargs="*", help="ids from `mp review`")
    approve.add_argument("--all", action="store_true", help="every pending item")
    reject = sub.add_parser("reject", help="reject pending items (asks for the approver password)")
    reject.add_argument("ids", type=int, nargs="*", help="ids from `mp review`")
    reject.add_argument("--all", action="store_true", help="every pending item")
    reject.add_argument("--reason", required=True, help="why (recorded in the ledger)")
    sub.add_parser(
        "doctor", help="check that this instance, its approver and its backups are healthy"
    )
    sub.add_parser("backup", help="write an encrypted backup of this instance")
    vb = sub.add_parser("verify-backup", help="prove a backup file can be decrypted and read")
    vb.add_argument("file", type=Path)
    vb.add_argument("--key", type=Path, required=True, help="your age secret key file")
    admin = sub.add_parser("admin", help="admin-only commands").add_subparsers(
        dest="admin_cmd", required=True
    )
    init = admin.add_parser("init-instance", help="create an instance (needs MP_ADMIN_DSN)")
    init.add_argument("name")
    init.add_argument("--config-dir", type=Path, default=default_config_dir())
    admin.add_parser("migrate", help="apply pending migrations (needs MP_ADMIN_DSN)").add_argument(
        "name"
    )
    admin.add_parser(
        "set-approver", help="set the approver password, typed twice (needs MP_ADMIN_DSN)"
    ).add_argument("name")
    bs = admin.add_parser("backup-setup", help="create a backup key and folder for an instance")
    bs.add_argument("name")
    bs.add_argument("--backup-dir", type=Path, required=True)
    bs.add_argument("--key-file", type=Path, required=True)
    bs.add_argument("--config-dir", type=Path, default=default_config_dir())
    rs = admin.add_parser(
        "restore", help="restore a backup into a fresh instance (needs MP_ADMIN_DSN)"
    )
    rs.add_argument("name")
    rs.add_argument("file", type=Path)
    rs.add_argument("--key", type=Path, required=True)
    rs.add_argument("--pg-bin-dir", type=Path, help="folder with pg_restore (default: PATH)")
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
        if args.cmd == "doctor":
            return _doctor()
        if args.cmd == "backup":
            return _backup()
        if args.cmd == "verify-backup":
            return _verify_backup(args)
        if args.cmd in ("approve", "reject"):
            return _decide(args, approve=args.cmd == "approve")
        if args.admin_cmd == "migrate":
            return _migrate(args.name)
        if args.admin_cmd == "backup-setup":
            return _backup_setup(args.name, args.backup_dir, args.key_file, args.config_dir)
        if args.admin_cmd == "restore":
            return _restore(args)
        if args.admin_cmd == "set-approver":
            return _set_approver(args.name)
        return _init(args.name, args.config_dir)
    except (
        ConfigError,
        BackupError,
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
