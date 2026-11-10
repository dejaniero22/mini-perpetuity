# mini-perpetuity

Single-user, local memory layer with multiple isolated instances (separate accounts). Build plan: [docs/plan/BUILD_PLAN.md](docs/plan/BUILD_PLAN.md). Status: **M0 (foundations, no product code)**.

## Instances: separate accounts
One codebase, any number of named instances (e.g. `personal`, `work`, `test`). Each instance is a separate "account" with its own:

- **Database and login role** (`mp_<name>`, `mp_<name>_app`): an instance's role cannot connect to another instance's database.
- **Config file** (`~/.config/mini-perpetuity/<name>.toml`, mode `0600`) and **password** (Keychain or a `0600` file outside the repo; never in git).
- **Process**: one instance per MCP server process, chosen at startup via `MP_CONFIG`; it cannot switch.
- **Audit trail**: every ledger event records its instance.

Instance names: lowercase letters and digits, starting with a letter, up to 24 characters. Details and limits: [ADR-M1](docs/decisions/ADR-M1-instance-isolation.md).

### Suggested path (test, then work)
1. Create a throwaway `test` instance and learn the tool on synthetic content.
2. Use `personal` for your own notes.
3. Create `work` only when you are ready; keep it `synthetic_only` until you have checked your employer's AI/data-handling policy (backlog MB6).

### Client rule (plan §4.2)
Register **one instance per client profile/Project** (the `work` server only in a work-only Project, and so on). No database control stops a client that has several instances enabled from mixing their content in one answer.

### Limits
All instances share one local PostgreSQL cluster. Anyone with superuser access, or OS access to your Mac login, can read all of them.

## Data rules (plan §3)
No real data in git, ever. an employer-related instance uses synthetic content only until the employer AI/data-handling policy is checked (backlog MB6). No employee records, HRIS data, or medical/leave/compensation details in either instance.

See [GOVERNANCE.md](GOVERNANCE.md), [SECURITY.md](SECURITY.md), [docs/verification-backlog.md](docs/verification-backlog.md).
