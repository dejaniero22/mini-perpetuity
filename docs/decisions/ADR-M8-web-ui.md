# ADR-M8: A local web front end

- **Status:** Proposed. Requested and shaped by Rio on 2026-11-12; accepted when its PR merges. **Nothing is built by this ADR.** The UI is built after M7 (see Order of work).
- **Date:** 2026-11-12
- **Deviation:** the build plan (§1, "Not in scope") lists "HTTP or remote MCP; it is local stdio only". This ADR adds a local web surface served on this computer only. Recorded as D7 in `GOVERNANCE.md`. Remote access remains out of scope.

## Decisions (Rio, 2026-11-12)
1. Build a front end with an easy-to-use UI/UX; Rio does not want to use the system through the CLI for daily work.
2. **When:** right after M7, alongside real use. The "week of real use" therefore starts when the first UI version exists (or sooner, through the CLI, if Rio wants).
3. **Form:** a local web app in the browser, bound to this computer only.
4. **Security:** as secure as possible; "plenty of applications have secure web apps". Admin tasks (create an instance, set the approver password, upgrade, backups) should also be possible in the UI, not left to the terminal.
5. **Approvals:** typed in the UI. Rio chose this knowing it passes the approver password through the browser (ADR-M6b's terminal-only property is replaced by the controls below).
6. **Transport:** local **HTTPS** (not plain HTTP on localhost).
7. **Approval factor: the typed password only** (Rio, 2026-11-12, replacing the earlier passkey-plus-password choice): not every device has a fingerprint reader and the system must be accessible. No passkey is built; MB16 is withdrawn.
8. **First version:** search and browse; add and review notes; timeline view; audit log, status and instances.
9. **Method:** research the current recommended practice from primary sources before building. Done; findings below.

## What was verified (2026-11-12) **[V]**, with sources
Read from the standards bodies' and projects' own pages or repositories (not summaries).

| Finding | Source |
|---|---|
| OWASP ASVS latest stable is **5.0.0 (May 2025)**; it has Levels 1, 2, 3 (about 20%, 50%, 30% of requirements) and recommends organisations tailor it. | [ASVS repo](https://github.com/OWASP/ASVS), [What is the ASVS](https://github.com/OWASP/ASVS/blob/master/5.0/en/0x03-What-is-the-ASVS.md) |
| **WCAG 2.2** is a W3C Recommendation (12 Dec 2024). **WCAG 3.0** is only a Working Draft (10 Sep 2026): build to 2.2. | [WCAG 2.2](https://www.w3.org/TR/WCAG22/), [WCAG 3.0](https://www.w3.org/TR/wcag-3.0/) |
| **WCAG 3.3.8 (AA):** remembering a password is a cognitive test; a login step needs an alternative method, or a mechanism such as password-manager support and copy-and-paste. | [WCAG 2.2](https://www.w3.org/TR/WCAG22/) |
| **ASVS 6.2.7 (L1):** paste, browser password helpers and external password managers must be permitted. 6.3.1 (L1): brute-force controls. 6.3.3 (L2/L3): multi-factor, hardware-based at L3. **7.5.3 (L3):** extra authentication before highly sensitive operations. 7.2.4 (L1): a new session token on authentication. | [ASVS V6](https://github.com/OWASP/ASVS/blob/master/5.0/en/0x15-V6-Authentication.md), [ASVS V7](https://github.com/OWASP/ASVS/blob/master/5.0/en/0x16-V7-Session-Management.md) |
| **ASVS V3 (web front end)** has 31 requirements, including L1: `Secure` cookies (3.3.1), HSTS on all responses (3.4.1), safe rendering of text (3.2.2); L2: `SameSite`, `__Host-`, `HttpOnly`, a CSP, nosniff, a referrer policy, `frame-ancestors`; L3: COOP, a CSP report location. | [ASVS V3](https://github.com/OWASP/ASVS/blob/master/5.0/en/0x12-V3-Web-Frontend-Security.md) |
| `localhost`, `127.0.0.0/8`, `::1` and `*.localhost` are potentially trustworthy origins, so they are secure contexts (WebAuthn works). | [MDN](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts) |
| **Fetch Metadata** (`Sec-Fetch-Site`) is supported by all major browsers (since March 2023); servers should reject cross-site state-changing requests. A "site" ignores the port, so another local app on a different `localhost` port is **same-site**: trust only `same-origin` and `none`. **XSS defeats every CSRF defense**, so XSS prevention comes first. A token in a custom header is stronger than a hidden form field. | [web.dev](https://web.dev/articles/fetch-metadata), [OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) |
| **Strict CSP**: `script-src 'nonce-{RANDOM}' 'strict-dynamic'; object-src 'none'; base-uri 'none'`. Session cookies: `HttpOnly` mandatory, `SameSite` as defense in depth, `__Host-` recommended (it requires `Secure`). | [web.dev](https://web.dev/articles/strict-csp), [OWASP Session](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) |
| **WebAuthn PRF extension** returns a secret from a credential's pseudo-random function: usable as a key that only the same authenticator can reproduce. | [MDN](https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API/WebAuthn_extensions) |
| **Chrome DevTools Protocol** has a virtual WebAuthn authenticator with `hasPrf`, `hasUserVerification`: passkey flows can be tested in headless Chromium here (not real Touch ID). | [devtools-protocol](https://github.com/ChromeDevTools/devtools-protocol) |
| **Baseline**: "newly available" = supported by all core browsers; "widely available" = 30 months later. Defined by the WebDX Community Group. The rule for which CSS/JS features need no framework or polyfill. | [web.dev](https://web.dev/baseline) |
| **Starlette 1.10.0, uvicorn 0.54.0, python-multipart 0.0.33 are already installed** (dependencies of the MCP SDK; Python 3.14). Starlette's templates enable HTML autoescape by default. Jinja2 3.1.6 (BSD-3) and MarkupSafe 3.0.4 are not installed. | [PyPI](https://pypi.org/project/starlette/), [Starlette templating source](https://github.com/encode/starlette) |
| **htmx 2.0.11**: one 52 KB file, Zero-Clause BSD, no dependencies; **no published security advisories** (which means none known, not none). Its own security guidance: escape all user content; set `allowEval=false` (disables event filters, `hx-on:`, `js:` values), `historyCacheSize=0` (otherwise page snapshots are written to `localStorage`), keep `selfRequestsOnly`. (The "CVE-2026-3682-1" page in its docs is a parody, not a vulnerability.) | [htmx repo](https://github.com/bigskysoftware/htmx), [advisories](https://github.com/bigskysoftware/htmx/security/advisories) |
| Playwright for Python 1.63.0 exists on PyPI and matches the Chromium preinstalled in the build sandbox (Node Playwright 1.63.0, Chromium build 1243). `cryptography` 50.0.2 (already installed) supports certificate name constraints. | [PyPI](https://pypi.org/project/playwright/) |

## Not verified. Treat as open.
- Whether the PRF extension works with Touch ID on Rio's Mac, in the browsers Rio uses (MDN's support table is rendered by JavaScript and could not be read; secondary sources disagreed). **MB16 (withdrawn: password only).**
- How macOS and each browser handle a locally generated certificate authority (and whether Safari, Chrome and Firefox all accept it). **MB17.**
- Whether a least-privilege setup role (create databases and roles, not superuser) can run this repository's migrations and `init-instance`, and whether a read-only backup role can run `pg_dump`. **MB18.**
- Whether `<instance>.localhost` names resolve to the loopback address in every browser Rio uses. **MB19.**
- Chrome's Local Network Access rules for requests from public sites to localhost (secondary sources only; the official page is on a host outside the allowed list). **MB20.**
- That automated accessibility checks (axe-core) cover only part of WCAG 2.2: they do not replace keyboard and screen-reader testing on Rio's Mac. **MB21.**

## Security target
**ASVS 5.0.0 Level 2** across the chapters that apply to a single-user local web app, plus the Level 3 items for the approval step (6.3.3 hardware-based factor, 7.5.3 step-up for highly sensitive operations, 3.4.8 COOP, 3.4.7 CSP reporting). The first deliverable of the UI step is a tailored checklist (which ASVS itself recommends), listing each requirement as met, not applicable (with a reason) or deliberately deviated from (with a reason), and a test for each that can be tested. Accessibility target: **WCAG 2.2 AA**.

## Design (proposed)
**Stack: server-rendered HTML with progressive enhancement.**
- Python, **Starlette** (already installed), **Jinja2** (the one new Python dependency; autoescape on), the existing database layer unchanged.
- **htmx 2.0.11 vendored** (one file, its hash recorded), configured with `allowEval=false`, `historyCacheSize=0`, `selfRequestsOnly=true`; no inline scripts or handlers; no third-party scripts, fonts or CDNs, ever.
- **Plain CSS** limited to Baseline widely-available features (light and dark colour schemes, reduced motion, visible focus, targets large enough for WCAG 2.5.8). No npm toolchain, no framework build step: the smallest supply chain that meets the goals, and nothing that needs a bundler to read.
- WebAuthn needs a small amount of JavaScript; it is written by hand (no framework), under the same strict CSP.

**Request protection (every request, all pages):**
- Bind to the loopback address only; **Host header allowlist**; HTTPS only.
- **Fetch Metadata policy:** accept only `same-origin` and `none`; reject `same-site` and `cross-site`; for requests without the header, require a matching `Origin`. Plus an anti-CSRF token in a custom header or form field.
- **Strict nonce-based CSP**, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `frame-ancestors 'none'`, `Cross-Origin-Opener-Policy: same-origin`, `Cache-Control: no-store` on pages that show memory.
- All stored text is rendered as text (escaped), never as HTML, and shown as untrusted data (as in the MCP server and the CLI).

**Local HTTPS (Rio's choice).** A per-install local certificate authority, restricted by name constraints to `localhost` names, generated with the already-installed `cryptography` package; private keys `0600`; short-lived leaf certificate. Rio must trust the authority once on the Mac (**MB17**). With HTTPS: `Secure` and `__Host-` cookies and HSTS all work, so the L1 and L2 cookie and HSTS requirements can be met rather than waived.

**Session.** The UI is opened with a one-time link containing a random token printed by `mp ui`; it is exchanged for a `__Host-` cookie (`HttpOnly`, `Secure`, `SameSite=Strict`), a new session token on authentication, an inactivity timeout and an absolute lifetime (both documented), and explicit logout.

**One instance, one origin.** Each instance gets its own origin (`<instance>.localhost`, port per launch; **MB19**), so cookies, storage and tabs are separated by the browser itself, and `personal` and `work` can never appear in the same page. The instance name and a distinct colour are always visible.

**Approvals (decision 5, 7).**
- **Typed approver password:** a form POST over HTTPS; paste and password managers are **permitted** (ASVS 6.2.7, WCAG 3.3.8), so "stored nowhere" now means "this system never stores it", not "you may not store it". The server uses it for one batch (one transaction, as in M6b) and discards it: never logged, never in a URL, never kept in a session, generic error messages, and server-side attempt limits with lockout (ASVS 6.3.1) in addition to the 16-character minimum. An extension in the browser could still read the field; the README recommends a separate browser profile without extensions for this UI.
- **No passkey (decision 7).** The typed password is the only approval factor, so everything above about unlocking a credential with a passkey no longer applies; the research notes on PRF are kept only as background.

**Admin tasks in the UI (decision 4).** Creating an instance, setting the approver password, upgrading and backups happen in the UI through **least-privilege roles, never a stored superuser**: a setup role (create databases and roles) and a backup role (read-only), each unlocked by a fresh confirmation for every action (ASVS 7.5.3). The UI process never holds the PostgreSQL superuser login. **This depends on MB18.** Until it is verified, these tasks stay in the terminal and the UI says so.

**First version (decision 8).**
- **Search and browse:** the search box front and centre, results with source and validity, an item page with its history of corrections.
- **Add and review notes:** quick capture (a pending note, with the SSN/secret check explained clearly), and a review inbox for your notes and AI proposals, AI text marked as untrusted; batch approve and reject, as in M6b.
- **Timeline:** two controls, "true on" and "known on", answering the two bi-temporal questions.
- **Audit log, status and instances:** the ledger, approver state, registered AI clients and doctor/backup status (from M7), and an instance switcher.
- Ideas to refine after testing (not committed): a "connect an AI client" wizard that produces the config entry; a keyboard shortcut for quick capture.

**Not in scope for version 1:** more than one user; remote access; OAuth; a mobile layout beyond what responsive CSS gives for free; AI chat inside the UI.

## Testing and method
- **Playwright for Python pinned to 1.63.0** (matches the sandbox's Chromium), run headless; **axe-core** for the automated part of WCAG 2.2 AA; the **virtual authenticator** for passkey flows; tests for every security header, the Fetch Metadata policy (cross-site and same-site requests refused), CSP (no inline script runs), cookie attributes, the Host allowlist, escaping of hostile stored text, session timeouts and lockout.
- The usual process: deliberate weakenings (remove a header, accept `same-site`, drop the CSRF check, allow `unsafe-eval`) must make a test fail; gitleaks before each push; one PR per step.
- **What cannot be tested here:** real Touch ID, Safari, macOS certificate trust, keyboard and screen-reader use on a Mac. Those are listed in the verification backlog and need Rio.

## Order of work
1. Accept this ADR (merge).
2. **M7** (`mp doctor`, backups), built so the UI can call it.
3. **M8 spikes** on Rio's Mac, small and scripted: MB17 (certificate trust), MB18 (least-privilege roles), MB19 (`*.localhost`).
4. **M8a** the secure shell (HTTPS, headers, session, request protection, tests); then **M8b** the four screens.
5. Real use through the UI.

## Consequences and limits
- It widens the attack surface: a local web server exists, and the approver password passes through a browser. The controls above reduce, not remove, that. An extension in the browser, or something running as Rio's OS user that edits the `mp` code, shell startup files or the PostgreSQL files, is still out of reach of every control here (same limit as ADR-M6b).
- Local HTTPS adds a one-time trust step and a certificate to keep alive (renewal).
- Version-1 accessibility is verified automatically and by keyboard testing here; a full screen-reader pass needs Rio's Mac.
- This ADR can be wrong where it rests on unverified items; those are the spikes, and the plan changes if a spike fails.

## Spike status (2026-11-12)
Results are in [docs/spikes/M8-spikes.md](../spikes/M8-spikes.md). MB17 and MB19 are verified in Chrome 157 on Rio's Mac; MB18 holds on Linux (a separate backup role is not needed; the setup role owns the tables and must be unlocked per action). MB16 is withdrawn. Still open: Safari, Firefox and Windows for MB17/MB19, MB18 on Rio's PostgreSQL, and whether Windows must be supported at all.
