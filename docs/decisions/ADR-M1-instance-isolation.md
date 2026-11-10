# ADR-M1: Instance isolation model and INV-5 deviation

- **Status:** Accepted (Rio, 2026-11-10)
- **Date:** 2026-11-10
- **Amended:** same day. Instances are now *any number of named instances*, not a fixed pair (deviation D6). `personal` and `work` are just the first two names.

## Decision
One codebase, **N named instances**, each behaving like a separate account. Per instance, nothing is shared:

| Item | Naming / rule |
|---|---|
| Instance name | `^[a-z][a-z0-9]{0,23}$` (lowercase letters/digits, starts with a letter; no `_`/`-`, so derived identifiers cannot collide). Validated at startup and by `mp admin init-instance`. |
| Config file | `~/.config/mini-perpetuity/<name>.toml`, mode `0600`, selected by `MP_CONFIG`. The `instance` field inside must equal the name in the file name. |
| Process | One instance per process; no default fallback; cannot switch instances at runtime. |
| Database | `mp_<name>` |
| App role | `mp_<name>_app` (separate login per instance; never superuser, never owner of the tables) |
| Password | Unique per instance; stored in macOS Keychain (item name includes the instance) or a `0600` file outside the repo (final: ADR-M3). Never in git. |
| Ledger | Every event row carries the `instance` name; a CHECK constraint ties it to the database's instance. |
| MCP registration | One server entry per instance; the entry name includes the instance name. |
| Data policy | Each instance declares `data_policy = "synthetic_only"` (default) or `"real"` in its config. Switching to `real` is a deliberate edit; for an employer-related instance it requires the policy check (MB6) first. **[Inference]** *Proposed for M1; not yet designed in detail.* |

M1 also revokes `CONNECT`/`TEMPORARY` from `PUBLIC` on every instance database, and `EXECUTE` from `PUBLIC` on guard functions, because PostgreSQL grants these to `PUBLIC` by default **[V]** (https://www.postgresql.org/docs/18/ddl-priv.html, cited by the plan; not re-read in M0). Each app role gets access to its own database only.

## Required tests (M1), for every pair of instances
- Role A cannot connect to database B (and the reverse), tested over at least three instances, not only two.
- `PUBLIC` has no `CONNECT` on any instance database.
- A config whose `instance` field disagrees with its file name, or whose name fails the regex, is refused.
- `init-instance` on an existing name refuses rather than reusing the database or role.

## INV-5 deviation
The main authorization service is replaced by the instance boundary plus one default-deny `authorize()` function (M4).

## Rejected
- One database with an `instance_id` column: one missed filter leaks across instances. **[Inference]**
- Two (or N) PostgreSQL clusters: stronger, costlier; revisit if the single-cluster setup proves weak, especially for a work instance. **[Inference]**

## Known limits
- Single cluster: a PostgreSQL superuser, or anyone with OS access to your Mac account, can read every instance. Instances separate *accounts*, not *machines*. For stronger separation (for example, work data on a separate cluster or machine), revisit after M7. **[Inference]**
- A client with several instance servers enabled in one conversation can mix them; no database control prevents it. Rule: one instance per client profile/Project. M7 `mp doctor` warns where the client config can be read (MB5).
