# ADR-M0: Adopt a governance subset and inherit main ADRs

- **Status:** Accepted (Rio, 2026-11-10)
- **Date:** 2026-11-10

## Decision
Adopt the main Guide's priority order, evidence labels and the invariant subset in plan §5. Inherit main ADRs 0004 (Python 3.14), 0006 (uv, Ruff, mypy --strict, pytest, pip-audit, pinned), 0007 (PostgreSQL 18), 0008 (pgvector + built-in FTS), 0016 (append-only ledger layers 1–2; native PG 18 for development).

## Consequences
- Separate repo; main phase gates are unaffected. Nothing is copied to main; lessons enter main via its ADR/PR process.
- Deviations are listed in `GOVERNANCE.md`.
- **[U]** The main ADR texts were not available when this was written; the inheritance list is taken from the plan, not re-read. Re-check before M1.
