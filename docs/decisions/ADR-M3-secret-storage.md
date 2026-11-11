# ADR-M3: Secret storage for database credentials

- **Status:** Accepted (Rio, 2026-11-11)
- **Date:** 2026-11-11

## Decision
Each instance's app-role password is kept in a **`0600` file** next to its config (`~/.config/mini-perpetuity/<name>.pw`), referenced by `password_file` in `<name>.toml`. The config loader refuses a config or password file that is readable by group or others. `mp admin init-instance` generates the password (`secrets.token_urlsafe(32)`) and writes the file; nothing is ever written to the repository.

The macOS Keychain is **deferred**, not rejected.

## Why
- Already implemented and tested since M1 (`tests/test_config.py`); works the same on macOS and Linux CI.
- Using the Keychain from Python needs a library whose name, version, license and Python 3.14 support are unverified (backlog MB8), and it cannot be tested in the Linux build sandbox. **[U]**

## Consequences and limits
- Anyone who can read your user's files (or a backup of `~/.config`) can read the password. Disk encryption (FileVault) protects it only when the Mac is off or locked. **[Inference]**
- The password also appears in the `CREATE ROLE` statement sent at creation, so it can appear in server logs if statement logging is on (not checked).
- No rotation command yet. To rotate, change the role's password as admin and rewrite the file.
- The admin DSN (`MP_ADMIN_DSN`) is never stored by the tool; keep it out of AI clients and shell history.

## Revisit
When MB8 is verified, or if the Mac is shared or the `work` instance holds real data.
