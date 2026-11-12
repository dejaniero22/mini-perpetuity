"""M8 spike server: one local HTTPS page that answers MB16, MB17 and MB19 on your own Mac.

    uv run python spikes/m8_spike_server.py

It makes a throwaway certificate authority limited to localhost names, signs a 7-day certificate
for personal.localhost, work.localhost and test.localhost, DELETES the authority's private key,
and serves one test page on 127.0.0.1 only. It stores and sends nothing: the page shows a report
you copy and paste back. Everything it made is removed when you stop it (Ctrl-C).
"""

import argparse
import datetime
import ipaddress
import os
import shutil
import signal
import ssl
import sys
import tempfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_NAMES = ("personal", "work", "test")
LEAF_DAYS, CA_DAYS = 7, 30


@dataclass(frozen=True)
class Material:
    ca_cert: Path
    leaf_cert: Path
    leaf_key: Path


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _name(common: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])


def make_ca(tag: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """A CA that can only vouch for localhost names and loopback addresses (name constraints)."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now() - datetime.timedelta(minutes=5)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(f"Mini Perpetuity spike CA {tag}"))
        .issuer_name(_name(f"Mini Perpetuity spike CA {tag}"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )  # fmt: skip
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_network("127.0.0.0/8")),
                    x509.IPAddress(ipaddress.ip_network("::1/128")),
                ],
                excluded_subtrees=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def issue_leaf(
    ca_key: ec.EllipticCurvePrivateKey,
    ca_cert: x509.Certificate,
    dns_names: list[str],
    days: int = LEAF_DAYS,
    ips: list[str] | None = None,
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = _now() - datetime.timedelta(minutes=5)
    san: list[x509.GeneralName] = [x509.DNSName(n) for n in dns_names]
    san += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips or []]
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(dns_names[0]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )  # fmt: skip
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _write(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def build_material(directory: Path, instances: tuple[str, ...]) -> Material:
    """Write ca.pem, leaf.pem and leaf.key (0600) into `directory` (0700). The CA key is never
    written: it exists only in memory while the leaf is signed."""
    directory.chmod(0o700)
    ca_key, ca_cert = make_ca(os.urandom(3).hex())
    names = ["localhost", *(f"{i}.localhost" for i in instances)]
    leaf_key, leaf_cert = issue_leaf(ca_key, ca_cert, names)
    pem = serialization.Encoding.PEM
    m = Material(directory / "ca.pem", directory / "leaf.pem", directory / "leaf.key")
    _write(m.ca_cert, ca_cert.public_bytes(pem), 0o644)  # public: only its trust matters
    _write(m.leaf_cert, leaf_cert.public_bytes(pem), 0o644)
    _write(
        m.leaf_key,
        leaf_key.private_bytes(
            pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        0o600,
    )
    return m


# ---- the page ----

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Mini Perpetuity: M8 spike</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/spike.css"></head>
<body><main>
<h1>M8 spike <span id="where"></span></h1>
<p>Nothing here is stored or sent anywhere. Each section fills in by itself or has one button.
When done, press <b>Show report</b> and copy the text back to Claude. Do this in each browser
you use, on each of these addresses: <span id="links"></span></p>
<section><h2>MB17 / MB19: you can read this page</h2>
<p id="basic">checking&hellip;</p></section>
<section><h2>MB16: passkey with PRF (Touch ID)</h2>
<p>Creates a <b>test</b> passkey for this address and asks for it twice. Delete it afterwards
(Passwords app or browser settings: &ldquo;Mini Perpetuity spike&rdquo;).</p>
<button id="create">1. Create test passkey</button>
<button id="use">2. Use it (asks for Touch ID 3 times)</button>
<p id="prf" role="status"></p></section>
<section><h2>Report</h2><button id="report">Show report</button>
<textarea id="out" rows="14" readonly aria-label="Report to copy"></textarea></section>
</main><script src="/spike.js"></script></body></html>
"""

CSS = (
    "body{font:16px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:42rem;padding:0 1rem}"
    "button{font:inherit;padding:.4rem .8rem;margin:.2rem}textarea{width:100%;font:13px monospace}"
    "section{border-top:1px solid #888;margin-top:1rem}"
)

JS = r"""
'use strict';
const $ = (id) => document.getElementById(id);
const report = {browser: navigator.userAgent, origin: location.origin};
const hex4 = async (buf) => [...new Uint8Array(await crypto.subtle.digest('SHA-256', buf))]
  .slice(0, 4).map((x) => x.toString(16).padStart(2, '0')).join('');
const rnd = (n) => crypto.getRandomValues(new Uint8Array(n));
const salt = (s) => new TextEncoder().encode(s);
const label = location.hostname.split('.')[0];
$('where').textContent = '(' + location.host + ')';
const names = (document.cookie || '').split(';').map((c) => c.split('=')[0].trim()).filter(Boolean);
report.secureContext = isSecureContext;
report.https = location.protocol === 'https:';
report.cookieNamesSeenOnArrival = names;
try {
  document.cookie = '__Host-spike-' + label + '=1; Secure; Path=/; SameSite=Strict';
  report.hostCookieWorks = document.cookie.includes('__Host-spike-' + label + '=1');
} catch (e) { report.hostCookieWorks = 'error: ' + e.name; }
report.webauthnAvailable = !!(window.PublicKeyCredential && navigator.credentials);
$('basic').textContent = 'Loaded over ' + location.protocol + ' at ' + location.host
  + '; secure context: ' + isSecureContext + '; __Host- cookie: ' + report.hostCookieWorks
  + '; WebAuthn: ' + report.webauthnAvailable;
const port = location.port ? ':' + location.port : '';
$('links').replaceChildren(...['personal', 'work', 'test', ''].flatMap((n) => {
  const a = document.createElement('a');
  a.href = 'https://' + (n ? n + '.' : '') + 'localhost' + port + '/';
  a.textContent = (n ? n + '.' : '') + 'localhost';
  return [a, document.createTextNode('  ')];
}));
let credId = null;
$('create').addEventListener('click', async () => {
  try {
    const cred = await navigator.credentials.create({publicKey: {
      challenge: rnd(32), rp: {name: 'Mini Perpetuity spike', id: location.hostname},
      user: {id: rnd(16), name: 'spike', displayName: 'Mini Perpetuity spike (delete me)'},
      pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],
      authenticatorSelection: {residentKey: 'preferred', userVerification: 'required'},
      extensions: {prf: {eval: {first: salt('mp-spike-1')}}}, timeout: 60000}});
    const ext = cred.getClientExtensionResults();
    credId = cred.rawId;
    report.prfCreate = {enabled: ext.prf ? ext.prf.enabled : 'extension missing',
      outputOnCreate: !!(ext.prf && ext.prf.results && ext.prf.results.first),
      attachment: cred.authenticatorAttachment || 'unknown'};
    $('prf').textContent = 'Created. prf.enabled = ' + report.prfCreate.enabled;
  } catch (e) {
    report.prfCreate = {error: e.name + ': ' + e.message};
    $('prf').textContent = e.name;
  }
});
const getPrf = async (s) => {
  const a = await navigator.credentials.get({publicKey: {
    challenge: rnd(32), rpId: location.hostname, userVerification: 'required',
    allowCredentials: [{type: 'public-key', id: credId}],
    extensions: {prf: {eval: {first: salt(s)}}}, timeout: 60000}});
  const r = a.getClientExtensionResults().prf;
  return r && r.results && r.results.first ? hex4(r.results.first) : null;
};
$('use').addEventListener('click', async () => {
  if (!credId) { $('prf').textContent = 'Create the passkey first.'; return; }
  try {
    const a = await getPrf('mp-spike-1');
    const b = await getPrf('mp-spike-1');
    const c = await getPrf('mp-spike-2');
    report.prfGet = {outputReturned: a !== null, sameSaltSameOutput: a !== null && a === b,
      otherSaltDifferent: c !== null && c !== a};
    $('prf').textContent = JSON.stringify(report.prfGet);
  } catch (e) {
    report.prfGet = {error: e.name + ': ' + e.message};
    $('prf').textContent = e.name;
  }
});
$('report').addEventListener('click', () => {
  $('out').value = JSON.stringify(report, null, 2); $('out').select();
});
"""

ROUTES: dict[str, tuple[str, str]] = {
    "/": ("text/html; charset=utf-8", HTML),
    "/spike.css": ("text/css; charset=utf-8", CSS),
    "/spike.js": ("text/javascript; charset=utf-8", JS),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Opener-Policy": "same-origin",
    # Deliberately no Strict-Transport-Security: a spike must not leave browsers pinned to HTTPS.
}


class Handler(BaseHTTPRequestHandler):
    allowed_hosts: frozenset[str] = frozenset()

    def _send(self, code: int, ctype: str, body: str) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if (self.headers.get("Host") or "").lower() not in self.allowed_hosts:
            self._send(421, "text/plain", "unknown host\n")  # blocks DNS-rebinding style names
        elif self.path in ROUTES:
            ctype, body = ROUTES[self.path]
            self._send(200, ctype, body)
        else:
            self._send(404, "text/plain", "not found\n")

    do_HEAD = do_GET  # noqa: N815

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        print(f"{self.address_string()} {format % args}", file=sys.stderr)


def make_server(
    material: Material, instances: tuple[str, ...], port: int = 8443
) -> ThreadingHTTPServer:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(material.leaf_cert, material.leaf_key)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # loopback only, never 0.0.0.0
    port = server.server_address[1]
    hosts = ["localhost", *(f"{i}.localhost" for i in instances)]
    handler = type("Bound", (Handler,), {"allowed_hosts": frozenset(f"{h}:{port}" for h in hosts)})
    server.RequestHandlerClass = handler
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--port", type=int, default=8443)
    p.add_argument("--instances", nargs="+", default=list(DEFAULT_NAMES))
    args = p.parse_args(argv)
    instances = tuple(args.instances)
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # same clean-up as Ctrl-C
    workdir = Path(tempfile.mkdtemp(prefix="mp-spike-"))
    try:
        m = build_material(workdir, instances)
        server = make_server(m, instances, args.port)
        port = server.server_address[1]
        print(f"Throwaway CA certificate (public): {m.ca_cert}")
        print("Trust it ONCE in the login keychain, then open the addresses below (macOS, [U]):")
        print(
            "  security add-trusted-cert -r trustRoot "
            f"-k ~/Library/Keychains/login.keychain-db {m.ca_cert}"
        )
        print(
            "Remove it afterwards: Keychain Access > login > Certificates > "
            "'Mini Perpetuity spike CA' > Delete."
        )
        for i in instances:
            print(f"  https://{i}.localhost:{port}/")
        print("Ctrl-C to stop; all files above are deleted when you do.")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
