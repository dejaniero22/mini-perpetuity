# mini-perpetuity

A small, single-user, local **memory layer for AI clients**. It runs as any number of isolated, named **instances** (for example `personal`, `work`, `test`), each behaving like a separate account. It exists to (1) give you a working personal memory and (2) serve as a small-scale sandbox for the larger Perpetuity Machine design.

> Status: **early build.** See [Status](#status). Nothing here is production software.

## Quick start (M1)

Requires Python 3.14, [uv](https://docs.astral.sh/uv/), and a local PostgreSQL 18 where you have an admin login. **[U]** Native install steps for macOS are still unverified (backlog MB1).

```bash
uv sync
```

Create an instance (admin connection only; the app role never creates instances):

```bash
export MP_ADMIN_DSN='postgresql://<admin-user>@localhost:5432/postgres'
uv run mp admin init-instance test
```

This creates database `mp_test` and role `mp_test_app`, revokes `PUBLIC` access, and writes `~/.config/mini-perpetuity/test.toml` and `test.pw` (both mode `0600`). Then check it:

```bash
MP_CONFIG=~/.config/mini-perpetuity/test.toml uv run mp status
```

After upgrading the code, bring an existing instance up to date (idempotent; admin connection only):

```bash
uv run mp admin migrate test
```

Add a memory (a human decision or a source record; text that looks like an SSN or a secret is refused):

```bash
export MP_CONFIG=~/.config/mini-perpetuity/test.toml
uv run mp add --type human_decision --content "Use PostgreSQL for storage."
uv run mp add --type source_record --content "Retention is 7 years" --source "policy X, section Y" --valid-at 2024-01-01T00:00:00+00:00
```

There is no read-back command yet (retrieval arrives in M4, the MCP server in M5). Until then, inspect with `psql` as the admin role.

`mp status` exits non-zero if the config is missing, malformed, too permissive, or points at a database that belongs to another instance.

### Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
MP_ADMIN_DSN='postgresql://<admin-user>@localhost:5432/postgres' uv run pytest
```

Database tests need `MP_ADMIN_DSN` (they create and drop throwaway `t…` instances); without it they skip locally and **fail in CI**. CI runs them on PostgreSQL 18.

## How it is meant to work

- Memory lives in **PostgreSQL**, one database per instance. An MCP server (local stdio only) is the single way an AI client reads memory; AI-written memory is quarantined until you approve it.
- Plan: [docs/plan/BUILD_PLAN.md](docs/plan/BUILD_PLAN.md). Rules: [GOVERNANCE.md](GOVERNANCE.md). Decisions: [docs/decisions/](docs/decisions/).

## Instances: separate accounts

One codebase, any number of named instances. Each instance has its own:

- **Database and login role** (`mp_<name>`, `mp_<name>_app`): one instance's role cannot connect to another instance's database.
- **Config file** (`~/.config/mini-perpetuity/<name>.toml`, mode `0600`) and **password** (never in git).
- **Process**: one instance per MCP server process, chosen at startup via `MP_CONFIG`; it cannot switch.
- **Audit trail**: every ledger event records its instance.

Names are lowercase letters and digits, start with a letter, at most 24 characters. Full model and limits: [ADR-M1](docs/decisions/ADR-M1-instance-isolation.md).

**Suggested path:** try a throwaway `test` instance on synthetic content; then use `personal`; create `work` only when ready, and keep it `synthetic_only` until you have checked your employer's AI and data-handling policy (backlog item MB6).

### Client rule
Register **one instance per client profile or Project** (the `work` server only in a work-only Project, and so on). No database control stops a client with several instances enabled from mixing their content in one answer.

### Limits
All instances share one local PostgreSQL cluster. A superuser, or anyone with access to your Mac login, can read all of them.

## Data rules

- No real data in git, ever: the repo holds code, docs and synthetic fixtures only.
- No employee records, HRIS exports, leave or medical details, SSNs, or compensation for named people in any instance.
- Do not send content to third parties (embedding APIs, hosted models) before the plan's M5b decision.

## Status

| Step | Scope | State |
|---|---|---|
| M0 | Repository foundations (docs, governance, hook, CI) | done |
| M1 | Instances and databases | done |
| M2 | Append-only event ledger | done |
| M3 | Memory objects (bi-temporal, provenance), `mp add` | in review |
| M4–M7 | Retrieval, MCP server, write-back, daily use | planned |

Open verification items: [docs/verification-backlog.md](docs/verification-backlog.md).

## Repository layout

| Path | Contents |
|---|---|
| `GOVERNANCE.md` | Rules subset, evidence labels, deviations from the main project, PR process |
| `CLAUDE.md` | Instructions for the AI coding assistant |
| `SECURITY.md` | Security notes and reporting |
| `CHANGELOG.md` | Keep a Changelog, one entry per step |
| `src/mini_perpetuity/` | `config.py` (loader), `admin.py` (instance creation, migrations), `db.py`, `ledger.py` (append-only events), `memory.py` (bi-temporal objects), `seatbelt.py`, `cli.py`, `migrations/` |
| `tests/` | Config, instance-isolation, ledger and memory tests (real PostgreSQL), hook tests |
| `pyproject.toml`, `uv.lock` | Pinned project and dependencies |
| `docs/plan/` | The build plan |
| `docs/decisions/` | ADRs (ADR-M0, ADR-M1, …) |
| `docs/learning/` | One short Learning Note per step |
| `docs/verification-backlog.md` | Facts still to verify, with the step that needs them |
| `.claude/` | AI assistant permissions and the `protect-paths` hook |
| `.github/workflows/` | CI |

## Evidence labels

**[V]** checked against a primary source · **[V2]** secondary source only · **[U]** not verified · **[Inference]** reasoned judgment.
