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

This creates database `mp_test` and two roles, revokes `PUBLIC` access, and writes `~/.config/mini-perpetuity/test.toml` and `test.pw` (both mode `0600`):

- `mp_test_app`: the everyday role (its password is in `test.pw`). It can read, create **pending** memory and write audit events. It can never make memory trusted.
- `mp_test_approver`: the only role that can approve or reject. It cannot log in until you give it a password that **you type and nothing stores**:

```bash
uv run mp admin set-approver test      # asks twice; at least 16 characters (four or five random words is easy and strong)
MP_CONFIG=~/.config/mini-perpetuity/test.toml uv run mp status     # shows approver=set
```

After upgrading the code, bring an existing instance up to date (idempotent; admin connection only):

```bash
uv run mp admin migrate test
```

Add, review and approve (text that looks like an SSN or a secret is refused):

```bash
export MP_CONFIG=~/.config/mini-perpetuity/test.toml
uv run mp add --type human_decision --content "Use PostgreSQL for storage."      # asks for nothing
uv run mp add --type source_record --content "Retention is 7 years" --source "policy X, section Y" --valid-at 2024-01-01T00:00:00+00:00
uv run mp review                    # everything pending: your notes and any AI proposals
uv run mp approve 1 2               # shows what you are approving, then asks for the approver password, once
```

`mp add` stores a **pending** note, so adding is instant. A note is searchable only after you approve it. `mp approve` takes several ids at once (or `--all`), shows each item, asks for the password once, and approves them all in one step or none. There is deliberately no "approve everything of mine" option: the author label on a pending item is just text, and approving it blind would turn your password into a rubber stamp.

Search what you have stored (approved, currently valid memory only):

```bash
uv run mp search postgresql
uv run mp search '"storage engine" -mysql' --limit 5
uv run mp search postgresql --history      # also superseded versions of corrected memories
```

Query syntax: words (all must match), `"quoted phrase"`, `-excluded`, `or`. Matching is by whole word with English stemming ("stores" matches "store"), not by prefix: `postgres` does not match `PostgreSQL`.

**Every search is recorded in the append-only ledger, including the query text, and cannot be erased.** Obvious secrets in a query are refused, but do not search for anything you would not want kept forever.

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

## Connect an AI client

Each AI client you connect is registered **by name** in the instance's config and runs its own server process, bound to one instance. It can search and fetch approved memory and **propose** new notes. It cannot approve anything, and it cannot see `sensitive` objects.

1. **Register the client** in the instance config (for example `~/.config/mini-perpetuity/personal.toml`):

   ```toml
   clients = ["claude-desktop"]
   ```

2. **Add a server entry to the client.** For Claude Desktop, in `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`), then fully quit and reopen the app. Use absolute paths:

   ```json
   {
     "mcpServers": {
       "memory-personal": {
         "command": "/ABSOLUTE/PATH/TO/mini-perpetuity/.venv/bin/mp",
         "args": ["serve", "--client", "claude-desktop",
                  "--config", "/ABSOLUTE/PATH/TO/.config/mini-perpetuity/personal.toml"]
       }
     }
   }
   ```

   For Claude Code the equivalent is `claude mcp add memory-personal -- /ABSOLUTE/PATH/TO/.venv/bin/mp serve --client claude-code --config /ABSOLUTE/PATH/TO/personal.toml`, with `claude-code` added to `clients`.

3. **One instance per client profile or Project** (the client rule above). Never register the `work` server where the `personal` server is, or the reverse.

Tested here: the exact command above (absolute `mp`, `serve --client --config`, no environment variables) against the MCP Python SDK's own client over stdio. **[V]** Not tested: the entries inside Claude Desktop or Claude Code themselves; their config formats come from the SDK's documentation (a secondary source for those apps). **[V2]**

**Limits to understand before you connect a client:**
- The `--client` name is a label in the client's own config; a local stdio server cannot verify who launched it.
- **A client that can run shell commands as your OS user can bypass the server** (it can read the `0600` password file and connect to PostgreSQL directly). Registration only constrains what a client can do through the server. Details: [ADR-M5](docs/decisions/ADR-M5-mcp-server.md).
- Limits: 60 requests per minute per client; denied requests are capped at 20 per minute per instance, after which they are refused but no longer recorded.
- Everything a client searches for is recorded in the permanent ledger.

## Review and approve

Everything that becomes trusted memory goes through one step you control. Your own notes (`mp add`) and AI proposals (`propose_memory`) both start **pending**: invisible to search and to every client, including the one that wrote them. You decide with the CLI:

```bash
uv run mp review                                          # pending items; stored text is shown after "|"
uv run mp approve 3 4 5                                   # one password prompt for the whole batch
uv run mp reject 6 7 --reason "that is not what I decided"
```

- The **approver password** is typed at a terminal. It is never read from a file, an argument, an environment variable, a pipe, or the MCP server, and it is stored nowhere (the database keeps only a hash). If you lose it: `mp admin set-approver NAME` resets it.
- Your decision and reason are recorded in the ledger (`human_decision`), and only the approver role can write that event.
- An AI proposal is an `ai_inference` or a `summary`, must name its source (`derived_from`), is at most 2,000 characters, and a client can have at most 50 waiting. Approval does not change who wrote it: it stays attributed to the client.
- Read what you approve: the tool cannot tell a wrong note from a right one.

**What this protects.** The database itself refuses to let the everyday role (the MCP server, `mp search`, `mp add`, a script, or any AI that merely runs commands as you) approve anything, create trusted memory, change a status, or write a `human_decision` event. Only the approver role can, and its password exists only in your head.

**What it does not protect.** It raises the bar; it is not a wall against something that actively attacks your own machine setup. Anything running as your OS user could edit the `mp` code or your shell startup files to capture the password the next time you type it, or, where PostgreSQL runs as your user, change PostgreSQL's own configuration or files. For that you would need PostgreSQL or the approval step under a different OS user or on another machine. It also does not protect confidentiality: the everyday role can still read everything in an instance, `sensitive` objects included. Details: [ADR-M6b](docs/decisions/ADR-M6b-approver-role.md).

## Backups and health

```bash
uv run mp doctor                                   # what is healthy, what needs attention, and the fix
uv run mp admin backup-setup NAME --backup-dir DIR --key-file KEYFILE   # once per instance
uv run mp backup                                   # encrypted dump of this instance
uv run mp verify-backup FILE --key KEYFILE         # proves the backup can be opened
uv run mp admin init-instance NAME                 # after a total loss: fresh, empty instance, same name
uv run mp admin restore NAME FILE --key KEYFILE    # needs MP_ADMIN_DSN; add --pg-bin-dir if needed
```

- Backups are `pg_dump` output encrypted with [age](https://age-encryption.org) to the **public** key in your instance config. The secret key is written once to the file you name and is never printed, never in the config, and refused inside the backup folder. **Copy it somewhere that survives losing this computer; without it no backup can be opened.**
- Needs PostgreSQL client tools of the same major version as the server or newer (`pg_dump`, `pg_restore`), on `PATH` or via `pg_bin_dir = "/path/to/bin"` in the instance config.
- Restore goes only into a fresh instance with the **same name**, in one transaction, and records a `backup.restored` ledger event. Nothing restored is promoted: approved stays approved, pending stays pending.
- Backups contain everything in the instance, `sensitive` objects included. Do not put real `work` data in until the employer-policy check (MB6) is done.
- **Where to keep the backup folder (macOS) [U, not tested on a Mac]:** on an encrypted volume (FileVault covers the startup disk; an encrypted external disk or an encrypted disk image are alternatives), ideally on a different disk than PostgreSQL's data. Check the steps against Apple's current documentation and tell me what differs (MB22).
- Design and limits: [ADR-M7](docs/decisions/ADR-M7-backups.md). Week-one checklist including the restore drill: [docs/real-use-checklist.md](docs/real-use-checklist.md).

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
| M3 | Memory objects (bi-temporal, provenance), `mp add` | done |
| M4 | Retrieval: full-text search, default-deny authorization, audit, `mp search` | done |
| M5 | MCP server (stdio), per-client principals, rate limits | done |
| M6 | Write-back with quarantine: `propose_memory`, `mp review`/`approve`/`reject` | done |
| M6b | Stronger approval: separate approver role, password typed once per batch | done |
| M7 | Daily use: `mp doctor`, encrypted backups and restore | done |
| M8 spikes | Small tests that decide the web UI design ([results](docs/spikes/M8-spikes.md)): Chrome on Mac verified; Safari, Firefox, Windows open | in review |
| M8 | Local web front end (HTTPS; search, add and review, timeline, audit and status) | decision record proposed ([ADR-M8](docs/decisions/ADR-M8-web-ui.md)); built after M7 |

Open verification items: [docs/verification-backlog.md](docs/verification-backlog.md).

## Repository layout

| Path | Contents |
|---|---|
| `GOVERNANCE.md` | Rules subset, evidence labels, deviations from the main project, PR process |
| `CLAUDE.md` | Instructions for the AI coding assistant |
| `SECURITY.md` | Security notes and reporting |
| `CHANGELOG.md` | Keep a Changelog, one entry per step |
| `src/mini_perpetuity/` | `config.py` (loader), `admin.py` (instance creation, migrations), `db.py`, `ledger.py` (append-only events), `memory.py` (bi-temporal objects), `search.py` (retrieval), `authz.py` (default-deny gate), `access.py` (gate + rate limits), `mcp_server.py` (MCP server), `propose.py` / `review.py` (write-back and the owner's decisions; `db.py` holds the approver connection), `backup.py` (encrypted backup, verify, restore), `doctor.py` (health checks), `seatbelt.py`, `cli.py`, `migrations/` |
| `spikes/` | Throwaway experiment scripts for the M8 decisions (not part of the product) |
| `tests/` | Config, instance-isolation, ledger, memory, search, authorization, rate-limit, quarantine and MCP contract tests (real PostgreSQL), hook tests |
| `pyproject.toml`, `uv.lock` | Pinned project and dependencies |
| `docs/plan/` | The build plan |
| `docs/decisions/` | ADRs (ADR-M0, ADR-M1, …) |
| `docs/learning/` | One short Learning Note per step |
| `docs/verification-backlog.md` | Facts still to verify, with the step that needs them |
| `.claude/` | AI assistant permissions and the `protect-paths` hook |
| `.github/workflows/` | CI |

## Evidence labels

**[V]** checked against a primary source · **[V2]** secondary source only · **[U]** not verified · **[Inference]** reasoned judgment.
