"""Instance config: one instance per process, read once, no fallback."""

from __future__ import annotations

import os
import pwd
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"[a-z][a-z0-9]{0,23}")
DATA_POLICIES = ("synthetic_only", "real")
_KEYS = {"instance", "host", "port", "password_file", "data_policy", "clients"}
CLIENT_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")


class ConfigError(Exception):
    pass


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise ConfigError(
            f"invalid instance name {name!r}: lowercase letters/digits, "
            "starting with a letter, at most 24 characters"
        )
    return name


def db_name(instance: str) -> str:
    return f"mp_{validate_name(instance)}"


def role_name(instance: str) -> str:
    return f"mp_{validate_name(instance)}_app"


def check_private(path: Path, what: str) -> None:
    """Refuse files readable by group/other (must be 0600-style)."""
    try:
        st = path.stat()
    except OSError as e:
        raise ConfigError(f"cannot read {what} {path}: {e.strerror}") from e
    if not path.is_file() or st.st_mode & 0o077:
        raise ConfigError(f"{what} {path} must be a regular file with mode 0600")


@dataclass(frozen=True)
class Config:
    instance: str
    host: str
    port: int
    password_file: Path
    data_policy: str
    owner: str  # "human:<OS user owning the config file>"
    clients: tuple[str, ...] = ()  # registered AI clients, each allowed as "client:<name>"

    @property
    def dbname(self) -> str:
        return db_name(self.instance)

    @property
    def user(self) -> str:
        return role_name(self.instance)

    def password(self) -> str:
        check_private(self.password_file, "password file")
        return self.password_file.read_text().strip()


def _owner(uid: int) -> str:
    try:
        return f"human:{pwd.getpwuid(uid).pw_name}"
    except KeyError:
        return f"human:{uid}"


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    raw_path = env.get("MP_CONFIG")
    if not raw_path:
        raise ConfigError("MP_CONFIG is not set")
    path = Path(raw_path)
    check_private(path, "config file")
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"malformed config {path}: {e}") from e

    if unknown := set(data) - _KEYS:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")
    for key, typ in (("instance", str), ("host", str), ("password_file", str), ("port", int)):
        if not isinstance(data.get(key), typ) or isinstance(data.get(key), bool):
            raise ConfigError(f"config key {key!r} is missing or not a {typ.__name__}")
    instance = validate_name(data["instance"])
    if path.stem != instance:
        raise ConfigError(f"config file name {path.name!r} does not match instance {instance!r}")
    if not 1 <= data["port"] <= 65535:
        raise ConfigError("port out of range")
    policy = data.get("data_policy", "synthetic_only")
    if policy not in DATA_POLICIES:
        raise ConfigError(f"data_policy must be one of {DATA_POLICIES}")
    clients = data.get("clients", [])
    if (
        not isinstance(clients, list)
        or not all(isinstance(c, str) and CLIENT_RE.fullmatch(c) for c in clients)
        or len(set(clients)) != len(clients)
    ):
        raise ConfigError("clients must be a list of unique names: lowercase letters, digits, -")
    pw = Path(data["password_file"])
    if not pw.is_absolute():
        raise ConfigError("password_file must be an absolute path")
    return Config(
        instance, data["host"], data["port"], pw, policy, _owner(path.stat().st_uid), tuple(clients)
    )
