"""Shared test helpers: the approver connection, and a CLI runner that can type a password."""

import os
import pty
import select
import subprocess
import sys
import time

import psycopg

from mini_perpetuity.config import Config
from mini_perpetuity.db import approver_connect

# The approver password tests use. Real ones are typed by a person and stored nowhere.
TEST_APPROVER_PASSWORD = "correct horse battery staple 42"


def trusted_connect(cfg: Config) -> psycopg.Connection[tuple[object, ...]]:
    """A connection as the approver: the only role that may create or decide trusted memory."""
    return approver_connect(cfg, TEST_APPROVER_PASSWORD)


def run_cli(
    cfg: Config,
    *args: str,
    passwords: list[str] | None = None,
    stdin_text: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    """Run `mp ...` as a subprocess against `cfg`.

    With `passwords`, the process gets a real pseudo-terminal and each password is typed when
    the program prompts for one (so the actual prompt code runs, not a mock). Without it, the
    process has no terminal at all, like a script or an AI's shell tool.
    """
    environ = {
        "MP_CONFIG": str(cfg.password_file.with_suffix(".toml")),
        "PATH": os.environ["PATH"],
        **(env or {}),
    }
    cmd = [sys.executable, "-m", "mini_perpetuity", *args]
    if passwords is None:
        return subprocess.run(
            cmd, env=environ, capture_output=True, text=True, timeout=timeout,
            input=stdin_text, stdin=None if stdin_text is not None else subprocess.DEVNULL,
            start_new_session=True,  # no controlling terminal, like an AI's shell tool
        )  # fmt: skip
    master, slave = pty.openpty()
    # New session: no controlling terminal, so the prompt uses our pty and can never reach (or hang
    # on) the terminal pytest was started from.
    proc = subprocess.Popen(
        cmd, env=environ, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )  # fmt: skip
    os.close(slave)
    assert proc.stderr is not None
    seen = b""
    deadline = time.monotonic() + timeout
    try:
        for n, password in enumerate(passwords, start=1):
            while seen.count(b": ") < n and time.monotonic() < deadline:  # prompts end in ": "
                ready, _, _ = select.select([proc.stderr], [], [], 0.2)
                if ready:
                    chunk = os.read(proc.stderr.fileno(), 4096)
                    if not chunk:
                        break
                    seen += chunk
                elif proc.poll() is not None:
                    break
            os.write(master, (password + "\n").encode())
        out, err = proc.communicate(timeout=max(1.0, deadline - time.monotonic()))
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
    return subprocess.CompletedProcess(cmd, proc.returncode, out.decode(), (seen + err).decode())
