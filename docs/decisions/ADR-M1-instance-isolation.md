# ADR-M1: Instance isolation model and INV-5 deviation

- **Status:** Proposed (needs acceptance by Rio)
- **Date:** 2026-11-10

## Decision
One codebase, two instances (`personal`, `work`). Each instance = one process (config from `MP_CONFIG`, no default fallback), one PostgreSQL database, one app role. M1 revokes `CONNECT`/`TEMPORARY` from `PUBLIC` on both databases and `EXECUTE` from `PUBLIC` on guard functions, because PostgreSQL grants these to `PUBLIC` by default **[V]** (https://www.postgresql.org/docs/18/ddl-priv.html, cited by the plan; not re-read in M0).

## INV-5 deviation
The main authorization service is replaced by the instance boundary plus one default-deny `authorize()` function (M4).

## Rejected
- One database with an `instance_id` column: one missed filter leaks across instances. **[Inference]**
- Two PostgreSQL clusters: stronger, costlier; revisit if single-cluster proves weak. **[Inference]**

## Known limit
A client with both MCP servers enabled can mix instances; no DB control prevents it (plan §4.2; README rule; M7 `mp doctor` check).
