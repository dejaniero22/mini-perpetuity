# M8 spikes: what was tested, what you need to test

Four open questions from [ADR-M8](../decisions/ADR-M8-web-ui.md) decide how the web front end is built. The scripts are in `spikes/`; they use only synthetic data, store nothing, and clean up after themselves. Labels: **[V]** observed in this project's test environment; **[U]** not verified.

## Results so far (Linux sandbox, PostgreSQL 18.7, headless Chromium 153)

| Item | Question | Result |
|---|---|---|
| **MB18** | Can least-privilege roles do the admin tasks? | **Yes [V]** for a setup role (`LOGIN CREATEDB CREATEROLE`, not superuser) that runs `init-instance`, the migrations and `set-approver`, and for a read-only role that runs `pg_dump`. Details below. Rio's PostgreSQL still to be checked. |
| **MB19** | Do `<instance>.localhost` names reach the loopback address? | **Yes in Chromium on Linux [V]**: `personal`, `work` and `test` `.localhost` all loaded, secure context true, `__Host-` cookie works. **Safari, Firefox and Chrome on your Mac: open (below).** |
| **MB17** | Does a name-constrained local CA work? | **Partly [V]:** OpenSSL refuses a certificate from this CA for `example.com`, `evil.example.com`, `personal.localhost.evil.com` and a non-loopback IP ("permitted subtree violation"), and accepts `localhost` names and `127.0.0.1`. Chromium refuses the certificate until the CA is trusted (`ERR_CERT_AUTHORITY_INVALID`). **The one-time trust step on macOS and how each browser treats it: open.** |
| **MB16** | Does PRF with Touch ID work? | **Page logic verified [V]** with a virtual authenticator (same salt gives the same output, another salt a different one). **Touch ID and your browsers: open.** This decides whether passkey approval is built. |

### MB18 detail (what the design can rely on)
- The setup role created an instance, applied all migrations, set the approver password, and the everyday role then worked. It is not a superuser and **could not change a role it did not create**.
- **No separate backup role is needed.** Since migration 0008 the everyday role can already run `pg_dump`. A dedicated read-only role (`pg_read_all_data`) also works, but each instance revokes `CONNECT` from everyone, so it needs an explicit grant per instance.
- **Caution (finding):** the setup role *owns* the instance tables, so it can disable the ledger's guard triggers. It must therefore be unlocked per action and never held by the always-running UI process (this is what ADR-M8 already says; the test confirms it is necessary).
- Not tested: PostgreSQL versions other than 18.7, and a Mac install (Postgres.app, Homebrew).

## What to run on your Mac (about 10 minutes)

1. In the repository folder:
   ```bash
   uv run python spikes/m8_spike_server.py
   ```
2. It prints a certificate path and one `security add-trusted-cert …` command **[U: I could not test macOS commands; if it fails, open Keychain Access, drag the file into "login" > Certificates, double-click it, expand "Trust" and choose "Always Trust"]**. Run it once. This certificate authority can only vouch for `localhost` names and loopback addresses, and its private key is deleted immediately after the 7-day certificate is made.
3. For **each browser you use** (Safari, Chrome, Firefox, as applicable), open the three printed addresses (`personal.localhost`, `work.localhost`, `test.localhost`):
   - Does the page load with a closed padlock (no warning)? If a browser warns, note which.
   - On one address per browser press **1. Create test passkey**, then **2. Use it** (Touch ID three times), then **Show report**, and paste the text back to me.
4. Press Ctrl-C in the terminal: the server and its files are deleted.
5. Remove the certificate from your Mac: Keychain Access > login > Certificates > "Mini Perpetuity spike CA" > Delete.
6. Delete the test passkey (Passwords app, or the browser's passkey settings, entry "Mini Perpetuity spike").
7. For **MB18 on your PostgreSQL** (needs a superuser connection to a *test* PostgreSQL, not data you care about):
   ```bash
   MP_ADMIN_DSN='postgresql://YOU@localhost/postgres' uv run python spikes/mb18_roles.py
   ```
   It creates and removes its own throwaway roles and instance; paste the output back.

## What each outcome changes
- **MB16 fails or is unavailable:** no passkey is built; the typed approver password stays the only approval factor.
- **MB17 or MB19 fails in a browser you use:** I propose another origin scheme (for example one port per instance on `localhost`) and update ADR-M8 before building.
- **MB18 fails on your PostgreSQL:** admin tasks stay in the terminal and the UI says so.
