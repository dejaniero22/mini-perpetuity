# Changelog

Format: [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). One entry per plan step.

## [Unreleased]

### Added
- M0: repository foundations: README, governance subset with deviation list, `CLAUDE.md`, `SECURITY.md`, `.claude` settings and `protect-paths` hook with tests, ADR-M0 (governance subset) and ADR-M1 (N named instances, INV-5 deviation), verification backlog, CI with hook tests and gitleaks (actions pinned by SHA), Learning Note.
- M1: `uv` project, `mp` CLI (`status`, `admin init-instance`), config loader, per-instance database and role creation with `PUBLIC` privileges revoked, isolation tests on real PostgreSQL, CI PostgreSQL 18 service container (digest-pinned), Learning Note.
- M2: append-only `events` ledger per instance (app role `SELECT`/`INSERT` only, statement-level trigger blocking `UPDATE`/`DELETE`/`TRUNCATE`), `instance` stamped and checked on every event, `mp admin migrate` for existing instances, ledger tests, Learning Note.
- M3: bi-temporal `memory_objects` with provenance and epistemic types, enforced append-only (column-level grants plus guard trigger; corrections are new versions), `as_of` two-timeline query, `mp add`, ingest seatbelt, ledger events for add/correct, ADR-M3 (secret storage: `0600` file), Learning Note.
- M4: full-text search over approved, currently valid memory (`search_memory`, `mp search`), default-deny `authorize()` (owner-only, allow-list), every search and every denial written to the ledger, query validation and seatbelt, terminal-escape sanitizing on output, migration 0004 (search index; only approved versions may be superseded), Learning Note.
