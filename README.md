# mini-perpetuity

A small, single-user, local **memory layer for AI clients**. It runs as any number of isolated, named **instances** (for example `personal`, `work`, `test`), each behaving like a separate account. It exists to (1) give you a working personal memory and (2) serve as a small-scale sandbox for the larger Perpetuity Machine design.

> Status: **early build.** See [Status](#status). Nothing here is production software.

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
| M0 | Repository foundations (docs, governance, hook, CI) | in review |
| M1 | Instances and databases | not started |
| M2–M7 | Ledger, memory objects, retrieval, MCP server, write-back, daily use | planned |

Open verification items: [docs/verification-backlog.md](docs/verification-backlog.md).

## Repository layout

| Path | Contents |
|---|---|
| `GOVERNANCE.md` | Rules subset, evidence labels, deviations from the main project, PR process |
| `CLAUDE.md` | Instructions for the AI coding assistant |
| `SECURITY.md` | Security notes and reporting |
| `CHANGELOG.md` | Keep a Changelog, one entry per step |
| `docs/plan/` | The build plan |
| `docs/decisions/` | ADRs (ADR-M0, ADR-M1, …) |
| `docs/learning/` | One short Learning Note per step |
| `docs/verification-backlog.md` | Facts still to verify, with the step that needs them |
| `.claude/` | AI assistant permissions and the `protect-paths` hook |
| `tests/hooks/` | Tests for the hook |
| `.github/workflows/` | CI |

## Evidence labels

**[V]** checked against a primary source · **[V2]** secondary source only · **[U]** not verified · **[Inference]** reasoned judgment.
