"""The M8 spike tools: certificate rules, server hardening, and the MB18 role findings."""

import re
import socket
import ssl
import subprocess
from collections.abc import Iterator
from pathlib import Path
from threading import Thread

import pytest
from cryptography.hazmat.primitives import serialization
from m8_spike_server import (
    JS,
    ROUTES,
    Material,
    build_material,
    issue_leaf,
    make_ca,
    make_server,
)
from mb18_roles import run as run_mb18

INSTANCES = ("personal", "work")


def verify(ca: Path, leaf: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["openssl", "verify", "-CAfile", str(ca), str(leaf)],  # noqa: S607
        capture_output=True, text=True, check=False,
    )  # fmt: skip


def pem(path: Path, obj: object) -> Path:
    path.write_bytes(obj.public_bytes(serialization.Encoding.PEM))  # type: ignore[attr-defined]
    return path


# ---- certificates (MB17) ----


def test_material_is_private_and_the_ca_key_is_never_kept(tmp_path: Path) -> None:
    (tmp_path / "m").mkdir()
    m = build_material(tmp_path / "m", INSTANCES)
    assert sorted(p.name for p in m.leaf_key.parent.iterdir()) == ["ca.pem", "leaf.key", "leaf.pem"]
    assert m.leaf_key.stat().st_mode & 0o777 == 0o600
    assert m.leaf_key.parent.stat().st_mode & 0o777 == 0o700
    assert b"PRIVATE KEY" not in m.ca_cert.read_bytes() + m.leaf_cert.read_bytes()
    assert verify(m.ca_cert, m.leaf_cert).returncode == 0  # the leaf chains to the CA


def test_the_ca_can_only_vouch_for_localhost_names_and_loopback(tmp_path: Path) -> None:
    ca_key, ca_cert = make_ca("t")
    ca = pem(tmp_path / "ca.pem", ca_cert)
    good = issue_leaf(ca_key, ca_cert, ["localhost", "personal.localhost"], ips=["127.0.0.1"])
    assert verify(ca, pem(tmp_path / "good.pem", good[1])).returncode == 0
    cases: list[tuple[list[str], list[str] | None]] = [
        (["example.com"], None),
        (["evil.example.com"], None),
        (["personal.localhost.evil.com"], None),
        (["localhost"], ["10.0.0.1"]),
    ]
    for names, ips in cases:
        bad = verify(ca, pem(tmp_path / "bad.pem", issue_leaf(ca_key, ca_cert, names, ips=ips)[1]))
        assert bad.returncode != 0 and "permitted subtree violation" in bad.stdout + bad.stderr


def test_the_leaf_is_short_lived_and_server_only(tmp_path: Path) -> None:
    ca_key, ca_cert = make_ca("t")
    _, leaf = issue_leaf(ca_key, ca_cert, ["localhost"])
    assert (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days <= 7
    text = subprocess.run(
        ["openssl", "x509", "-noout", "-text", "-in", str(pem(tmp_path / "l.pem", leaf))],  # noqa: S607
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert "CA:FALSE" in text and "TLS Web Server Authentication" in text


# ---- the server (MB19 / MB16 page) ----


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Material, int, str]]:
    (tmp_path / "m").mkdir()
    m = build_material(tmp_path / "m", INSTANCES)
    server = make_server(m, INSTANCES, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield m, server.server_address[1], str(server.server_address[0])
    server.shutdown()
    server.server_close()


def fetch(
    m: Material, port: int, host: str, path: str = "/", method: str = "GET"
) -> tuple[int, dict[str, str], str]:
    ctx = ssl.create_default_context(cafile=str(m.ca_cert))
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname="personal.localhost") as tls:
            tls.sendall(
                f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
            )
            data = b""
            while chunk := tls.recv(65536):
                data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = {k.lower(): v for k, _, v in (ln.partition(": ") for ln in lines[1:])}
    return int(lines[0].split()[1]), headers, body.decode()


def test_the_page_is_served_with_strict_headers(served: tuple[Material, int, str]) -> None:
    m, port, _ = served
    status, h, body = fetch(m, port, f"personal.localhost:{port}")
    assert status == 200 and "M8 spike" in body
    csp = h["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "unsafe" not in csp
    assert "connect-src 'none'" in csp  # the page cannot send anything anywhere
    assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"
    assert h["referrer-policy"] == "no-referrer" and "strict-transport-security" not in h
    for path in ("/spike.js", "/spike.css"):
        assert fetch(m, port, f"work.localhost:{port}", path)[0] == 200


def test_unknown_hosts_paths_and_methods_are_refused(served: tuple[Material, int, str]) -> None:
    m, port, _ = served
    assert fetch(m, port, "evil.example.com")[0] == 421  # wrong Host name
    assert fetch(m, port, f"personal.localhost:{port + 1}")[0] == 421  # wrong port
    assert fetch(m, port, f"personal.localhost:{port}", "/secret")[0] == 404
    assert fetch(m, port, f"personal.localhost:{port}", "/", "POST")[0] in (501, 405)


def test_the_server_listens_on_loopback_only_and_requires_tls_13(
    served: tuple[Material, int, str],
) -> None:
    m, port, bound = served
    assert bound == "127.0.0.1"  # never 0.0.0.0: not reachable from other machines
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.load_verify_locations(str(m.ca_cert))
    old.maximum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with pytest.raises(ssl.SSLError):
            old.wrap_socket(raw, server_hostname="personal.localhost")


def test_the_page_never_sends_data_anywhere_and_has_no_inline_script() -> None:
    assert not re.search(r"fetch\(|XMLHttpRequest|sendBeacon|WebSocket|localStorage", JS)
    html = ROUTES["/"][1]
    assert re.findall(r"<script[^>]*>", html) == ['<script src="/spike.js">']
    assert "onclick" not in html and "style=" not in html


# ---- MB18 findings, against the real PostgreSQL ----


def test_least_privilege_roles_can_do_the_admin_tasks(admin_dsn: str, pg_bin: Path) -> None:
    steps = run_mb18(admin_dsn, pg_bin)
    failed = [f"{s.name}: {s.detail}" for s in steps if not s.ok]
    assert not failed, failed
    names = {s.name for s in steps}
    assert "setup role cannot change a role it did not create" in names
    assert "read-only role: cannot write" in names and "clean-up" in names
