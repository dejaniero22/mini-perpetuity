# Real-use checklist (week one)

The code is tested on Linux. This list is what only you can check, on your Mac, with your real use. Tick items; tell me what fails.

## Day 1 — setup (about 20 minutes)
- [ ] `mp admin init-instance personal` (and `test`), then `mp admin set-approver personal`; `mp status` shows `approver=set`.
- [ ] `mp admin backup-setup personal --backup-dir <folder on an encrypted volume> --key-file <path outside that folder>`.
- [ ] Copy the key file to a place that survives losing this Mac (password manager). Confirm you can find it.
- [ ] Add `pg_bin_dir` to `~/.config/mini-perpetuity/personal.toml` if `pg_dump --version` (PostgreSQL client 18 or newer) is not on `PATH`.
- [ ] `mp backup`, then `mp verify-backup <file> --key <key file>` prints `ok`.
- [ ] `mp doctor` shows no `FAIL` lines.

## During the week
- [ ] Add a few real *non-sensitive* notes (`mp add`), approve them (`mp review`, `mp approve`), search for them.
- [ ] Connect one AI client (README, "Connect an AI client"); ask it something your notes answer; let it propose one memory; review it.
- [ ] Make a backup on at least two different days. `mp doctor` should say how old the newest is.

## Day 7 — the restore drill (the only proof a backup works)
- [ ] Use a throwaway instance, because a restore needs a fresh instance with the same name: `mp admin init-instance drill`, `set-approver`, `backup-setup`, add and approve one note, `mp backup`.
- [ ] Destroy it as admin: `DROP DATABASE mp_drill WITH (FORCE); DROP ROLE mp_drill_app; DROP ROLE mp_drill_approver;`.
- [ ] `mp admin init-instance drill`, `mp admin set-approver drill`, `mp admin restore drill <file> --key <key>`, then `mp search` finds the note.
- [ ] Write down anything confusing; that is the input for M8.

## Questions to answer for the backlog
- MB14: does PostgreSQL run as your own macOS user, and does it use `scram-sha-256` for local TCP?
- MB22: do the macOS steps in the README work as written?
- MB13: do the client registration entries work in your apps?
