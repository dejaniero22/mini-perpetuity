# Learning Note: audit fixes

- **A security promise needs a test of the thing it depends on.** The approver password was tested against a PostgreSQL that checks passwords. Nobody asked whether the real install would. Postgres.app's default says no, and the whole design silently collapsed. The fix is a probe: try a wrong password and see whether it is refused. A system that depends on a property of its environment should check that property.
- **Refuse, do not just warn, where the claim would be false.** `mp approve` now stops when the password is not enforced. Letting you type a password that nothing checks would be security theatre.
- **psycopg gives no error code for a failed login.** The first version looked for SQLSTATE 28P01 and reported "unknown" against a real server. A throwaway PostgreSQL with trust login (made for the test, not the shared one) showed it. The probe now matches the server's message; a server in another language gives "unknown", shown as a warning.
- **Real fix verified, not assumed.** With a regex login rule for `mp_*` roles the probe turned ok, the typed password still approved, and the superuser login stayed passwordless.
- **Identity must not come from an environment variable.** `$LOGNAME=ceo` made `mp add` record the author as `human:ceo`. The owner is now the config file's owner, which the operating system vouches for. It was never a way to gain approval rights, but a history you can forge is a weak history.
- **Escape what you write into a config file.** A Windows path or a quote made the file unreadable. JSON string escapes are valid TOML, so one line fixes it.
- **A message that names a command must be tested against that command.** The backup hint used a flag that did not exist; a test now checks the hint's flags against the real command's help.
- **Time from the name, not the file.** Copying a backup changes its file time. The name carries the real time.
- **A weak test was found by mutation.** The "approve refuses" test also passed without the check, because a missing terminal gives the same exit code. It now checks the message.
- **Not verified.** Postgres.app on a Mac (where its login-rule file lives, whether updates keep the edit), and Windows.
