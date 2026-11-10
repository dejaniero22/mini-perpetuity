> **Note (added in this repo):** this is the plan as received (v0.1, 2026-11-10). It is kept verbatim below this note.
> Where it differs from the accepted ADRs, **the ADRs win**. Known difference: the plan assumes exactly two instances
> (`personal`, `work`); [ADR-M1](../decisions/ADR-M1-instance-isolation.md) generalises this to any number of named
> instances (deviation D6 in [GOVERNANCE.md](../../GOVERNANCE.md)).

# Mini Perpetuity: Build Plan

**Version:** 0.1 (draft, 2026-11-10)
**Owner:** Rio
**Status:** ready for M0. Every step after M0 waits on the step before it.
**Evidence labels** (same meaning as the main Guide §3): **[V]** checked against a primary source, **[V2]** secondary source only, **[U]** not verified, **[Inference]** reasoned judgment.

---

## 1. Purpose

A small, single-user version of the Perpetuity Machine. It runs as two separate **instances**:

| Instance | Holds | Used from |
|---|---|---|
| `personal` | Your own notes, decisions, plans, learning | A personal Claude Project or client profile |
| `work` | Your own work knowledge: procedures, your decisions, meeting notes in your words | A work-only Claude Project or client profile |

It has two jobs:
1. Give you a working personal memory layer.
2. Act as a learning sandbox for the main project's Phases 1, 3, 4, 5 and 6, on a small scale. **[Inference]**

### Not in scope
- More than one user, or sharing between users
- OpenFGA, an OIDC identity provider, a graph database, adaptive retrieval
- HTTP or remote MCP; it is local stdio only
- Any data from an HR system of record (see §3)

---

## 2. Relationship to the main Perpetuity Machine repo

- This is a **separate repository**. It is not a phase or step of the main project, so the main project's phase gates (`docs/progress/PHASES.md`, currently Phase 0) are not affected. **[Inference]**
- The mini repo adopts the main Guide's priority order, evidence labels, and the subset of invariants in §5. Its own short `GOVERNANCE.md` links back to the main Guide and lists every deviation.
- **No code moves from mini to main by copying.** A lesson or design learned here enters the main repo only through that repo's ADR and PR process.
- Inherited decisions (from the main repo's accepted ADRs, read 2026-11-10):
  - ADR-0004: Python 3.14 core
  - ADR-0006: uv, Ruff, mypy `--strict`, pytest, pip-audit, all pinned
  - ADR-0007: PostgreSQL 18
  - ADR-0008: pgvector plus built-in full-text search
  - ADR-0016: append-only ledger, using layers 1 and 2 only here
  - ADR-0016: native PostgreSQL 18 for development (Postgres.app or Homebrew). The install steps are still unverified (main backlog B67).

---

## 3. Data rules for each instance

| Rule | `personal` | `work` |
|---|---|---|
| Real data allowed | Yes, your own | Only your own work knowledge, in your words |
| Employee records, HRIS exports, leave or medical details, SSNs, compensation for named people | n/a | **Never.** California Civil Code §56.20 (employee medical information confidentiality, cited in the main Guide §7.2) covers this kind of data. |
| Employer AI and data-handling policy | n/a | **I cannot confirm what Academica's policy is.** Check it before the `work` instance holds real content. Until then, use synthetic content only. |
| Sent to third parties (embedding APIs, hosted models) | Only after the M5b decision | Not until the policy check above is done |
| Stored in git | Never; the repo holds code, docs and synthetic fixtures only | Never |

The ingest path in M3 rejects input that matches simple patterns (SSN-shaped strings, for example). This is a seatbelt, not a guarantee. **[Inference]**

---

## 4. Architecture

### 4.1 Isolation model: one codebase, two separate databases

```
           personal.toml                          work.toml
   (~/.config/mini-perpetuity/)          (~/.config/mini-perpetuity/)
                │                                      │
   MCP server process (stdio)            MCP server process (stdio)
   MP_CONFIG=.../personal.toml           MP_CONFIG=.../work.toml
                │                                      │
       role: mp_personal_app                  role: mp_work_app
                │                                      │
       database: mp_personal                  database: mp_work
                └────────── PostgreSQL 18 (local) ─────┘
```

- **One instance per process.** A process reads its config file once at startup, from an environment variable, and cannot switch instances. Without a valid config it exits; it does not fall back to a default.
- **Separate databases and roles.** By default PostgreSQL gives `PUBLIC` the `CONNECT` and `TEMPORARY` privileges on every new database, and `EXECUTE` on new functions. **[V]** Source: PostgreSQL 18 docs §5.8, https://www.postgresql.org/docs/18/ddl-priv.html. M1 therefore revokes `CONNECT` and `TEMPORARY` from `PUBLIC` on both databases and `EXECUTE` from `PUBLIC` on the guard functions, and grants each app role access to its own database only.
- **Not chosen:** one database with an `instance_id` column. A single missed filter would leak data between instances. **[Inference]**
- **Not chosen for now:** two separate PostgreSQL clusters. They would be stronger, but cost more to run. Revisit if the single-cluster setup proves weak. **[Inference]**

### 4.2 The client-side leak path

If one AI client has both MCP servers enabled in the same conversation, the model can combine personal and work memory in a single answer, and no database control prevents that. **[Inference]**
**Rule:** register the `work` server only in a work-only Claude Project or client profile, and the `personal` server only in a personal one. The README documents this, and the doctor command in M7 warns about it where the client config can be read. **[U]** Whether the client config can be read in a stable way is unverified.

### 4.3 MCP transport and credentials

- Local **stdio only**. The MCP spec says stdio implementations "SHOULD NOT" follow its OAuth authorization spec and should instead get credentials from the environment. **[V]** Source: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index
- The database password for each role is kept in the macOS Keychain, or in a `0600` file outside the repo. It never goes in `personal.toml`/`work.toml` if those files might ever be shared. Final choice: ADR-M3.

---

## 5. Which invariants the mini keeps

| Main invariant | In the mini | Proven by |
|---|---|---|
| INV-1 Append-only | **Keep.** The app role has no `UPDATE`, `DELETE` or `TRUNCATE` on ledger tables, plus a guard trigger (ADR-0016 layers 1 and 2). | M2 negative tests |
| INV-2 Bi-temporal (two timelines) | **Keep.** `valid_at`/`invalid_at` (true in the world) and `created_at`/`expired_at` (known to the system). | M3 "true on X" and "known on Y" tests |
| INV-3 Provenance | **Keep, simplified.** Columns modelled on PROV-O terms: `was_derived_from`, `generated_by_activity`, `attributed_to_agent`. | M3 tests: no object without provenance |
| INV-4 Epistemic types stay separate | **Keep.** `source_record`, `extracted_claim`, `ai_inference`, `human_decision`, `procedure`, `experience`, `summary`. | M3 constraint tests |
| INV-5 Authorization service | **Replaced.** Instance boundary plus one default-deny `authorize()` function in code. Deviation recorded in ADR-M1. | M1 crossover tests, M4 tests |
| INV-6 Filter before results reach a model | **Keep.** Retrieval returns only `approved` objects that are currently valid, unless asked for history. | M4 tests |
| INV-7 Quarantine | **Keep.** AI-written memory starts as `proposed`; only you approve it. | M6 poisoning tests |
| INV-8 Learn from validated use only | **Deferred.** | — |
| INV-9 PostgreSQL is the system of record | **Keep.** There is no graph database. | — |
| INV-10 Pointers before copies | **Keep for work:** store a pointer such as "policy X, section Y", not a copy of a sensitive record. | M3 review |
| INV-11 One gateway | **Keep.** The MCP server is the only way an AI reads or writes memory. | M5 |
| INV-12 Human approval | **Keep.** Approving a proposal is a human CLI action. | M6 |
| INV-13 Audit | **Keep, simplified.** Every read, search, proposal and approval writes an audit event to the ledger. | M2 to M6 tests |

---

## 6. Steps

One small PR per step. Each step has: tests (including negative tests), Ruff, mypy `--strict`, pytest, gitleaks, pip-audit once dependencies exist, a CHANGELOG entry (Keep a Changelog 1.1.0), and a short Learning Note.

### M0: Repository foundations (no product code)
- Create the repo `mini-perpetuity` (private) next to the main repo.
- Add README, `GOVERNANCE.md` (subset of the main Guide plus a list of deviations), `CLAUDE.md` (adapted from the main one), `SECURITY.md`, `.gitignore` (ignores `.env*`, `*.toml` under any `local/` folder, database dumps), and `CHANGELOG.md`.
- Copy the main repo's `.claude/settings.json` deny/ask rules and the `protect-paths.sh` hook, with the hook's tests.
- Add `docs/decisions/` with:
  - ADR-M0: adopt a governance subset and inherit main ADRs 0004, 0006, 0007, 0008, 0016
  - ADR-M1: instance isolation model (§4.1) and the INV-5 deviation
- Add `docs/verification-backlog.md`, seeded from §8.
- Add a CI workflow with gitleaks and the hook tests, with actions pinned by SHA (as in the main repo).
- **Exit:** CI runs green on a PR; ADR-M0 and ADR-M1 accepted by Rio.

### M1: Instances and databases
- `uv` project setup; CLI entry point `mp`.
- Config loader: reads `MP_CONFIG`, validates instance name ∈ {`personal`, `work`}, and exits non-zero on anything missing or malformed.
- `mp admin init-instance <name>`: SQL that creates the database and app role, revokes `PUBLIC` privileges (§4.1), and applies migrations. Run as the admin role, never as the app role.
- **Tests (against real PostgreSQL 18):**
  - `mp_personal_app` cannot connect to `mp_work`, and the reverse
  - `PUBLIC` has no `CONNECT` on either database
  - the process refuses to start with no config, a bad config, or an unknown instance name
- **Exit:** all crossover tests pass locally and in CI (PostgreSQL 18 service container per ADR-0016; image digest pinned).

### M2: Event ledger
- `events` table: id, occurred_at, actor, activity, payload (JSONB), instance.
- App role: `INSERT` and `SELECT` only. Guard trigger raises an error on `UPDATE`, `DELETE` and `TRUNCATE`.
- **Tests:** the app role's `UPDATE`, `DELETE` and `TRUNCATE` fail; the trigger fires even for a role that holds table privileges; the app role cannot disable the trigger.
- **Exit:** the main Phase 1 exit criterion, reproduced at small scale: the app role cannot silently change the ledger.

### M3: Memory objects (bi-temporal, with provenance)
- `memory_objects`: id, epistemic_type, content, valid_at, invalid_at, created_at, expired_at, provenance columns (§5), status (`proposed` | `approved` | `rejected` | `superseded`), sensitivity tag.
- Corrections create a new version and set `expired_at` on the old one. Nothing is updated in place.
- `mp add`: a human adds a `human_decision` or `source_record`, with the seatbelt patterns from §3.
- **Tests:** "what was true on date X" and "what did the system know on date Y" give different, correct answers on a synthetic fixture; inserts with no provenance or an unknown epistemic type are rejected.
- ADR-M3: secret storage for database credentials.

### M4: Retrieval (full-text search first)
- PostgreSQL built-in full-text search over approved, currently valid objects.
- `authorize(request)`: default deny; allows only the instance's owner on that instance's data.
- Every search writes an audit event recording the query, the IDs returned, and the time.
- **Tests:** `proposed` and `rejected` objects never come back; expired objects come back only on an explicit history request; malformed queries are rejected safely (injection cases).

### M5: Read-only MCP server (stdio)
- Tools: `search_memory`, `get_memory`. All returned content is marked as untrusted data in the tool output.
- **Library:** the official MCP Python SDK. **[U]** Package name, version, license and Python 3.14 support are to be checked and recorded in ADR-M5 before installing.
- Two client registrations, one per instance, following the §4.2 rule.
- **Tests:** a contract test showing the server only answers for its own instance; a stored memory containing prompt-injection text is returned as data and does not change server behaviour.

### M5b (decision only): Embeddings
- ADR-M5b: whether to add pgvector embeddings, and where they are computed (a local model or a hosted API). Decided separately for each instance, given §3. Default: **no embeddings for `work`**.

### M6: Write-back with quarantine
- MCP tool `propose_memory`: always creates `status = proposed`, `epistemic_type = ai_inference` (or `summary`), and provenance naming the AI client as the agent.
- CLI: `mp review` lists proposals; `mp approve <id>` and `mp reject <id> --reason` record a `human_decision` event.
- **Tests:** a proposed memory never shows up in search before approval; the MCP server has no tool or code path that approves; a proposal cannot set its own status or epistemic type.

### M7: Daily use
- `mp doctor`: checks database privileges, config file permissions (`0600`), and the §4.2 client rule where possible.
- Backup: `pg_dump` per instance to an encrypted location outside the repo. The method is decided in ADR-M7. **[U]**
- One week of real use on `personal`, with notes on what to change.

---

## 7. Decisions still open (yours)

1. Repo name. Proposed: `mini-perpetuity`, private, on your GitHub account.
2. M5b embeddings: none, local, or hosted, per instance.
3. When `work` may hold real (non-synthetic) content: after the Academica policy check in §3.
4. Which AI client(s) to register first: Claude Desktop, Claude Code, or both.

---

## 8. Starting verification backlog

| ID | Item | Needed by |
|---|---|---|
| MB1 | PostgreSQL 18 native install on your Mac (Postgres.app or Homebrew): steps and version (main backlog B67) | M1 |
| MB2 | PostgreSQL 18 Docker Official Image digest for CI (main backlog B68) | M1 |
| MB3 | Trigger behaviour vs. table owner and superuser; whether the app role can `ALTER TABLE ... DISABLE TRIGGER` | M2 |
| MB4 | MCP Python SDK: package name, current version, license, Python 3.14 support | M5 |
| MB5 | Whether Claude Desktop/Code client config can be read to check the §4.2 rule | M7 |
| MB6 | Academica AI and data-handling policy (you, not code) | before real `work` content |
| MB7 | pgvector version and Python 3.14 support for any embedding library, if M5b says yes | M5b |
| MB8 | Secret storage: macOS Keychain access from Python (library and license) | M3 |

---

## 9. Sources

- PostgreSQL 18 docs, §5.8 Privileges: https://www.postgresql.org/docs/18/ddl-priv.html [V, read 2026-11-10]
- MCP Specification 2026-07-28, Authorization: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index [V, read 2026-11-10]
- Main repo (perpetuity-machine), read 2026-11-10: `docs/governance/GOVERNING_GUIDE.md`; ADRs 0004, 0006, 0007, 0008, 0009, 0016; `docs/progress/PHASES.md`; `.claude/settings.json`; `CLAUDE.md`
- W3C PROV-O (term names only): https://www.w3.org/TR/prov-o/ [V2: cited by the main Guide; not re-read for this plan]
