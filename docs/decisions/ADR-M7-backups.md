# ADR-M7: Encrypted backups and `mp doctor`

- **Status:** Proposed. Accepted when its PR merges.
- **Date:** 2026-11-12
- **Plan step:** M7 (daily use).

## Problem
Memory only has value if it survives a lost disk, a broken PostgreSQL install or a mistake. Until now nothing copied it anywhere, and nothing told you when something was wrong.

## Decisions
1. **Backup = `pg_dump` custom format, encrypted with age, written once.** `mp backup` runs `pg_dump` as the everyday role, encrypts the output in memory to one or more **age public keys** stored in the instance config, and writes `mp_<name>_<UTC time>.dump.age` into the backup folder (folder `0700`, file `0600`). No plaintext dump ever touches the disk. An existing file is never overwritten.
2. **The secret key never lives in the config or the backup folder.** `mp admin backup-setup` creates the key once, writes it to a file you name (refused if that is inside the backup folder), and puts only the *public* key in the config. So the machine can make backups but cannot open them; you hold the key. Several recipients are supported (a spare key in a safe place).
3. **Standard format.** Files are ordinary age files: the reference `age` tool opens them (tested in CI with `age -d`), so recovery does not depend on this project's code.
4. **The password reaches `pg_dump`/`pg_restore` through a private temporary `pgpass` file** (`0600`, in a `0700` directory, deleted afterwards), never an argument or environment variable.
5. **Restore goes only into a fresh, empty instance of the same name** (`mp admin init-instance NAME`, then `mp admin restore NAME FILE --key KEY`). Data only, in one transaction, so a failure leaves the instance empty. It needs the admin connection, restores the ledger and memory as they were (nothing is promoted), and adds a `backup.restored` ledger event. A different name is refused by the database, because every ledger row carries its instance name.
6. **`mp verify-backup`** proves a file can be decrypted and is a complete dump (it needs your key). A backup that has never been checked is a hope, not a backup.
7. **`mp doctor` returns data** (`doctor.run_doctor()` gives a list of checks with status and a suggested fix), printed by the CLI and reusable by the future web UI (ADR-M8). It checks database reachability and ownership, applied migrations, approver password, `pg_dump` version, backup folder, and age of the newest backup (warns after 7 days). Exit code 1 only for failures, not warnings.
8. **Migration 0008.** (a) the everyday role may `SELECT` sequences, because `pg_dump` reads them (this answers the first half of MB18 on Linux: a role without superuser, ownership or write rights can dump; verified by test). (b) `instance_name()` gets a fixed `search_path`, because restores run with an empty one. Both are privilege-neutral: the role still cannot alter any sequence or structure.
9. **CI** gains one step (approved by Rio) that installs the PostgreSQL 18 client tools from the official PGDG apt repository and the `age` package; the runner image removes that repository after it is built. Everything else in the workflow is unchanged.

## Not decided here / limits
- **Where the backup folder lives.** Put it on an encrypted volume and, ideally, on a different disk or machine. That is your setup; the code only checks that the folder exists and is private. macOS steps are in the README, marked **[U]**.
- **Key custody.** If the key is lost, the backups are unreadable, by design. If it sits on the same disk as the backups, one lost laptop loses both.
- **Backups contain everything**, including `sensitive` objects. They are encrypted, but treat the key like the approver password.
- **Instance `work`:** do not put real work data into any backup until the employer-policy check (MB6) is done.
- Scheduling (cron/launchd) is not built; the web UI (M8) will offer "back up now" through the same functions. Until then run `mp backup` yourself.
- Restore requires the same PostgreSQL major version or newer, and has been tested only on Linux with PostgreSQL 18.7.

## Sources
- `pg_dump` / `pg_restore` options and the same-or-newer version rule: PostgreSQL 18 documentation, <https://www.postgresql.org/docs/18/app-pgdump.html> and <https://www.postgresql.org/docs/18/app-pgrestore.html> **[V]** (read in this project).
- age format and tool: <https://age-encryption.org> **[V]**; Python bindings `pyrage` 1.4.0 (pinned, with `pyrage-stubs`). Interoperability with the age CLI 1.1.1 was tested in both directions in the authoring sandbox **[V]**.
