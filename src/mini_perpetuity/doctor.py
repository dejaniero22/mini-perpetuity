"""Health checks, returned as data so the command line and the future web UI can both show them."""

import re
import time
from dataclasses import dataclass

from mini_perpetuity.admin import _migrations
from mini_perpetuity.backup import BackupError, pg_tool, tool_major
from mini_perpetuity.config import Config
from mini_perpetuity.db import app_connect, approver_is_set, verify_instance

STALE_DAYS = 7
BACKUP_FILE = re.compile(r"mp_(?P<name>[a-z][a-z0-9]*)_\d{8}T\d{6}Z\.dump\.age")


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # "ok", "warn" (works, but needs attention) or "fail" (broken)
    detail: str
    fix: str = ""


def _database(cfg: Config) -> tuple[list[Check], int | None]:
    try:
        with app_connect(cfg) as conn:
            verify_instance(conn, cfg)
            checks = [
                Check("database", "ok", f"{cfg.dbname} reachable and belongs to {cfg.instance}")
            ]
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            missing = [v for v, _ in _migrations() if v not in applied]
            checks.append(
                Check("schema", "ok", "up to date")
                if not missing
                else Check(
                    "schema",
                    "fail",
                    f"{len(missing)} migration(s) not applied",
                    f"mp admin migrate {cfg.instance}",
                )  # fmt: skip
            )
            checks.append(
                Check("approver", "ok", "password is set")
                if approver_is_set(conn, cfg)
                else Check(
                    "approver",
                    "warn",
                    "no approver password: nothing can be approved yet",
                    f"mp admin set-approver {cfg.instance}",
                )  # fmt: skip
            )
            row = conn.execute("SHOW server_version_num").fetchone()
            return checks, (int(str(row[0])) // 10000 if row else None)
    except Exception as e:  # noqa: BLE001 - any failure is a finding, not a crash
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        return [Check("database", "fail", first, "is PostgreSQL running? check host/port")], None


def _backups(cfg: Config, server: int | None) -> list[Check]:
    if cfg.backup_dir is None or not cfg.backup_recipients:
        return [
            Check(
                "backups", "warn", "not set up: a lost disk would lose all memory",
                f"mp admin backup-setup {cfg.instance} --backup-dir DIR --key-file FILE",
            )
        ]  # fmt: skip
    out: list[Check] = []
    try:
        client = tool_major(pg_tool(cfg.pg_bin_dir, "pg_dump"))
        pg_tool(cfg.pg_bin_dir, "pg_restore")
        ok = server is None or client >= server
        out.append(
            Check("pg_dump", "ok", f"version {client}")
            if ok
            else Check(
                "pg_dump",
                "fail",
                f"version {client} is older than the server ({server})",
                "install the PostgreSQL client tools matching the server and set pg_bin_dir",
            )  # fmt: skip
        )
    except BackupError as e:
        out.append(Check("pg_dump", "fail", str(e)))
    folder = cfg.backup_dir
    if not folder.is_dir():
        return [*out, Check("backup folder", "fail", f"{folder} does not exist", "create it")]
    mode = folder.stat().st_mode & 0o777
    if mode & 0o077:
        out.append(
            Check("backup folder", "warn", f"mode {mode:o} lets others in", f"chmod 700 {folder}")
        )
    mine = [
        p for p in folder.iterdir()
        if (m := BACKUP_FILE.fullmatch(p.name)) and m["name"] == cfg.instance
    ]  # fmt: skip
    if not mine:
        out.append(Check("last backup", "warn", "none yet", f"mp backup (instance {cfg.instance})"))
        return out
    newest = max(mine, key=lambda p: p.name)  # names sort by timestamp
    age_days = (time.time() - newest.stat().st_mtime) / 86400
    detail = f"{newest.name}, {age_days:.1f} days old, {newest.stat().st_size} bytes"
    stale = age_days > STALE_DAYS or newest.stat().st_size == 0
    out.append(
        Check("last backup", "warn" if stale else "ok", detail, "mp backup" if stale else "")
    )
    return out


def run_doctor(cfg: Config) -> list[Check]:
    checks, server = _database(cfg)
    return [*checks, *_backups(cfg, server)]


def format_report(checks: list[Check]) -> str:
    mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
    lines = []
    for c in checks:
        lines.append(f"[{mark[c.status]}] {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"       fix: {c.fix}")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "fail" for c in checks) else 0


__all__ = ["Check", "exit_code", "format_report", "run_doctor"]
