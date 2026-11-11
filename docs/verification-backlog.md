# Verification backlog

| ID | Item | Needed by | Status |
|---|---|---|---|
| MB1 | PostgreSQL 18 native install on Mac (Postgres.app/Homebrew): steps and version (main B67) | M1 | open |
| MB2 | PostgreSQL 18 Docker Official Image digest for CI (main B68) | M1 | **done 2026-11-10**: `postgres:18` = `sha256:e3d24f69…c42` (Docker Hub registry manifest, `library/postgres`). Digest will go stale; bump deliberately. |
| MB3 | Trigger behaviour vs table owner and superuser; whether the app role can `ALTER TABLE ... DISABLE TRIGGER` | M2 | **answered 2026-11-11** by tests on PostgreSQL 18.7 (local source build) and CI: the app role cannot disable/drop/replace the trigger or `SET session_replication_role`, even with `GRANT ALL` on the table. The table owner (the admin role) *can* disable it; layer 3 is out of scope. See `docs/learning/M2.md`. |
| MB4 | MCP Python SDK: package name, version, license, Python 3.14 support | M5 | open |
| MB5 | Can Claude Desktop/Code client config be read to check §4.2 rule | M7 | open |
| MB6 | Academica AI and data-handling policy (Rio, not code) | before real `work` content | open |
| MB7 | pgvector version and Python 3.14 support for any embedding library | M5b | open |
| MB8 | macOS Keychain access from Python (library and license) | M3 | **deferred by ADR-M3** (`0600` password file chosen); still unverified. |
| MB9 | Main ADR/Guide texts re-read; confirm inheritance list and §-citations | M1 | open |
| MB10 | Plan §2 lists ADR-0016 for both the ledger and native-PG dev; confirm which ADR covers which | M1 | open |
| MB11 | Postgres identifier/role-name limits and quoting for `mp_<name>` names; keychain item naming per instance | M1 | partly done: longest derived name `mp_`+24+`_app` = 31 chars (limit 63 per PostgreSQL docs, **[U]** not re-read); names quoted with `psycopg.sql.Identifier`. Keychain naming open (ADR-M3). |
| MB12 | Local PostgreSQL 18 for the dev loop | M1 | **done in the authoring sandbox 2026-11-11**: PostgreSQL 18.7 built from git tag `REL_18_7` (`--without-icu`; differs from the Docker image, which is Debian-built 18.x). Rio's Mac install is still MB1. |
