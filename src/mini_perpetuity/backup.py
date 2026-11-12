"""Encrypted backups of one instance (plan M7, ADR-M7): pg_dump -> age -> one file.

Backups are encrypted to PUBLIC keys, so making one needs no secret key; only restoring needs the
private key, which this system never keeps. The files are plain `age` files: they can be decrypted
with the standard `age` tool, with or without this project.
shortcut: the whole dump is held in memory, fine for a personal memory (kilobytes to a few
megabytes); stream through a pipe if an instance ever grows to gigabytes.
"""

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pyrage
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pyrage import x25519

from mini_perpetuity.config import Config, db_name, toml_string, validate_name
from mini_perpetuity.db import app_connect, verify_instance
from mini_perpetuity.ledger import append_event

TABLES_EXPECTED = ("events", "instance_meta", "memory_objects", "schema_migrations")


class BackupError(Exception):
    pass


def setup_hint(instance: str) -> str:
    return f"mp admin backup-setup {instance} --backup-dir DIR --key-file FILE"


@dataclass(frozen=True)
class Backup:
    path: Path
    size: int
    pg_dump_major: int


@dataclass(frozen=True)
class Verification:
    tables_with_data: tuple[str, ...]
    entries: int


@dataclass(frozen=True)
class Restored:
    memory_objects: int
    events: int


def pg_tool(bin_dir: Path | None, name: str) -> str:
    found = str(bin_dir / name) if bin_dir else shutil.which(name)
    if not found or not os.access(found, os.X_OK):
        raise BackupError(
            f"{name} not found: install the PostgreSQL client tools (same major version as the "
            "server, or newer) and, if they are not on PATH, set pg_bin_dir in the instance config"
        )
    return found


def tool_major(binary: str) -> int:
    out = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False).stdout  # noqa: S603
    found = re.search(r"(\d+)(?:\.\d+)*", out.split(")", 1)[-1])
    if not found:
        raise BackupError(f"cannot read the version of {binary}: {out.strip()!r}")
    return int(found.group(1))


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


@contextlib.contextmanager
def _passfile(database: str, user: str, password: str) -> Iterator[Path]:
    """A private libpq password file, deleted afterwards. Safer than PGPASSWORD or a command line,
    which other processes of the same user can read (libpq's documentation advises against it)."""
    tmp = Path(tempfile.mkdtemp(prefix="mp-pg-"))  # mode 0700
    try:
        path = tmp / "pgpass"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"*:*:{_esc(database)}:{_esc(user)}:{_esc(password)}\n")
        yield path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(argv: list[str], passfile: Path | None, stdin: bytes | None = None) -> bytes:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    if passfile is not None:
        env["PGPASSFILE"] = str(passfile)
    done = subprocess.run(  # noqa: S603 - argv is built from validated config values
        argv, input=stdin, capture_output=True, env=env, timeout=900, check=False
    )
    if done.returncode != 0:
        lines = done.stderr.decode(errors="replace").strip().splitlines()
        raise BackupError(f"{Path(argv[0]).name} failed: {' | '.join(lines[:4])}")
    return done.stdout


def _write_private(directory: Path, name: str, data: bytes) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = directory / f".{name}.part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        final = directory / name
        try:
            os.link(tmp, final)  # atomic, and never replaces an existing backup
        except FileExistsError:
            raise BackupError(
                f"a backup named {name} already exists; nothing was overwritten"
            ) from None
        tmp.unlink()
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return final


def backup_name(instance: str, when: datetime) -> str:
    return f"mp_{instance}_{when:%Y%m%dT%H%M%SZ}.dump.age"


def make_backup(cfg: Config, *, now: datetime | None = None) -> Backup:
    if cfg.backup_dir is None or not cfg.backup_recipients:
        raise BackupError(
            f"backups are not set up for {cfg.instance!r}: run `{setup_hint(cfg.instance)}`"
        )
    pg_dump = pg_tool(cfg.pg_bin_dir, "pg_dump")
    with app_connect(cfg) as conn:
        verify_instance(conn, cfg)
        row = conn.execute("SHOW server_version_num").fetchone()
    server = int(str(row[0])) // 10000 if row else 0
    client = tool_major(pg_dump)
    if client < server:
        raise BackupError(
            f"{pg_dump} is version {client} but the server is version {server}: "
            "pg_dump must be the same version as the server or newer"
        )
    argv = [
        pg_dump, "-w", "-h", cfg.host, "-p", str(cfg.port), "-U", cfg.user, "-d", cfg.dbname,
        "-Fc", "--no-owner", "--no-privileges",
    ]  # fmt: skip
    with _passfile(cfg.dbname, cfg.user, cfg.password()) as pf:
        dump = _run(argv, pf)
    recipients = [x25519.Recipient.from_str(r) for r in cfg.backup_recipients]
    blob = pyrage.encrypt(dump, recipients)
    when = now or datetime.now(UTC)
    path = _write_private(cfg.backup_dir, backup_name(cfg.instance, when), blob)
    with app_connect(cfg) as conn:
        append_event(
            conn, cfg, cfg.owner, "backup.created", {"file": path.name, "bytes": len(blob)}
        )
    return Backup(path, len(blob), client)


def read_identity(path: Path) -> x25519.Identity:
    try:
        text = path.read_text()
    except OSError as e:
        raise BackupError(f"cannot read the key file {path}: {e.strerror}") from e
    for line in text.splitlines():
        if line.startswith("AGE-SECRET-KEY-"):
            return x25519.Identity.from_str(line.strip())
    raise BackupError(f"no age secret key (AGE-SECRET-KEY-...) found in {path}")


def _decrypt(path: Path, identity: Path) -> bytes:
    try:
        return pyrage.decrypt(path.read_bytes(), [read_identity(identity)])
    except (pyrage.DecryptError, pyrage.IdentityError) as e:
        raise BackupError(f"cannot decrypt {path.name}: wrong key, or the file is damaged") from e
    except OSError as e:
        raise BackupError(f"cannot read {path}: {e.strerror}") from e


def verify_backup(path: Path, identity: Path, bin_dir: Path | None = None) -> Verification:
    """Decrypt a backup and check it is a readable PostgreSQL archive holding the expected data."""
    toc = _run([pg_tool(bin_dir, "pg_restore"), "--list"], None, _decrypt(path, identity))
    lines = toc.decode(errors="replace").splitlines()
    tables = sorted(
        {m.group(1) for ln in lines if (m := re.search(r"TABLE DATA public (\w+)", ln))}
    )
    missing = [t for t in TABLES_EXPECTED if t not in tables]
    if missing:
        raise BackupError(f"the backup is readable but lacks data for: {', '.join(missing)}")
    return Verification(tuple(tables), sum(1 for ln in lines if ln and not ln.startswith(";")))


def restore_backup(
    admin_dsn: str, name: str, path: Path, identity: Path, bin_dir: Path | None = None
) -> Restored:
    """Load a backup's memory and ledger into a FRESH instance (made with `mp admin init-instance`).

    Needs the admin (superuser) connection: the append-only guards must be switched off for a
    restore, and nothing else may be written meanwhile.
    """
    validate_name(name)
    pg_restore = pg_tool(bin_dir, "pg_restore")
    db = db_name(name)
    info = conninfo_to_dict(make_conninfo(admin_dsn, dbname=db))
    try:
        with psycopg.connect(make_conninfo(admin_dsn, dbname=db), autocommit=True) as c:
            meta = c.execute("SELECT instance FROM instance_meta").fetchone()
            if meta is None or meta[0] != name:
                raise BackupError(f"database {db} is not an instance named {name!r}")
            used = c.execute(
                "SELECT (SELECT count(*) FROM memory_objects) + (SELECT count(*) FROM events)"
            ).fetchone()
    except psycopg.OperationalError as e:
        raise BackupError(
            f"cannot open instance {name!r} ({str(e).splitlines()[0]}); create it first with "
            f"`mp admin init-instance {name}`"
        ) from None
    if used is None or used[0]:
        raise BackupError(
            f"instance {name!r} already holds data; restore only into a fresh instance "
            f"(`mp admin init-instance {name}` on an empty server)"
        )
    dump = _decrypt(path, identity)
    argv = [
        pg_restore, "-w", "--data-only", "--disable-triggers", "--single-transaction",
        "--no-owner", "-d", db,
    ]  # fmt: skip
    for key, flag in (("host", "-h"), ("port", "-p"), ("user", "-U")):
        if info.get(key):
            argv += [flag, str(info[key])]
    argv += ["-t", "memory_objects", "-t", "events"]
    with _passfile(db, str(info.get("user", "")), str(info.get("password", ""))) as pf:
        _run(argv, pf, dump)
    with psycopg.connect(make_conninfo(admin_dsn, dbname=db), autocommit=True) as c:
        for table in ("memory_objects", "events"):  # new rows must not reuse restored ids
            c.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "  # noqa: S608
                f"(SELECT max(id) FROM {table})) WHERE EXISTS (SELECT 1 FROM {table})"
            )
        counts = c.execute(
            "SELECT (SELECT count(*) FROM memory_objects), (SELECT count(*) FROM events)"
        ).fetchone()
        n_mem, n_ev = (int(str(counts[0])), int(str(counts[1]))) if counts else (0, 0)
        c.execute(
            "INSERT INTO events (actor, activity, payload, instance) VALUES "
            "('admin', 'backup.restored', jsonb_build_object('file', %s::text, "
            "'memory_objects', %s::int, 'events', %s::int), %s)",
            (path.name, n_mem, n_ev, name),
        )
    return Restored(n_mem, n_ev)


def setup_backup(config_file: Path, backup_dir: Path, key_file: Path) -> str:
    """Create an age key and point the instance config at a backup folder. Returns the public key.

    The secret key is written only to `key_file` (0600, never overwritten, never inside the
    backup folder) and is not printed. Only the public key goes into the config."""
    backup_dir, key_file = backup_dir.expanduser(), key_file.expanduser()
    if not backup_dir.is_absolute() or not key_file.is_absolute():
        raise BackupError("give absolute paths for the backup folder and the key file")
    if key_file.resolve().is_relative_to(backup_dir.resolve()):
        raise BackupError(
            "the key must not be kept inside the backup folder: "
            "the backup and its key would be lost or stolen together"
        )
    text = config_file.read_text()
    if re.search(r"^\s*(backup_dir|backup_recipients)\s*=", text, re.MULTILINE):
        raise BackupError(f"{config_file.name} already has backup settings; edit that file instead")
    identity = x25519.Identity.generate()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    _write_private(key_file.parent, key_file.name, (str(identity) + "\n").encode())
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.chmod(0o700)
    recipient = str(identity.to_public())
    with config_file.open("a") as f:
        f.write(
            f"\nbackup_dir = {toml_string(backup_dir)}\n"
            f"backup_recipients = [{toml_string(recipient)}]\n"
        )
    return recipient
