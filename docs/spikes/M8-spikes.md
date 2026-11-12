# M8 spikes: what was tested

Open questions from [ADR-M8](../decisions/ADR-M8-web-ui.md) that decide how the web front end is built. The scripts are in `spikes/`; they use only synthetic data, store nothing, and clean up after themselves. Labels: **[V]** observed; **[U]** not verified.

**Decision (Rio, 2026-11-12): password only, no fingerprint or passkey.** Not every device has a fingerprint reader and the system must be accessible, so the typed approver password is the only approval factor. MB16 (passkey PRF) is therefore withdrawn and its test was removed from the page.

## Results

| Item | Question | Result |
|---|---|---|
| **MB17** | Does a name-constrained local CA work? | **Chrome 157 on macOS (Rio's Mac) [V]:** after the one-time trust step the page loaded on all three addresses and Chrome reported "Connection is secure". Also **[V]** on Linux: OpenSSL refuses certificates from this CA for `example.com`, `evil.example.com`, `personal.localhost.evil.com` and a non-loopback IP ("permitted subtree violation"), and Chromium rejects the certificate until the CA is trusted. **Open:** Safari, Firefox, Chrome on Windows. |
| **MB19** | Do `<instance>.localhost` names reach the loopback address? | **Yes in Chrome 157 on macOS [V]** (`personal`, `work`, `test`) and in Chromium on Linux [V]. The report from `personal` showed secure context true, the `__Host-` cookie works, and **only that address's own cookie was present** after visiting all three, so instances do not share cookies. **Open:** Safari, Firefox, Windows. |
| **MB18** | Can least-privilege roles do the admin tasks? | **Yes [V]** on Linux with PostgreSQL 18.7: a non-superuser setup role (`LOGIN CREATEDB CREATEROLE`) runs `init-instance`, the migrations and `set-approver`, and cannot change roles it did not create. **Open:** Rio's PostgreSQL. |
| ~~MB16~~ | ~~Passkey with PRF~~ | Withdrawn (password only). |

The macOS user-agent string reports "Intel Mac OS X 10_15_7"; Chrome freezes that string, so it does not show the Mac's processor or macOS version.

### MB18 detail (what the design can rely on)
- The setup role created an instance, applied all migrations, set the approver password, and the everyday role then worked.
- **No separate backup role is needed.** Since migration 0008 the everyday role can already run `pg_dump`. A dedicated read-only role (`pg_read_all_data`) also works, but each instance revokes `CONNECT` from everyone, so it needs an explicit grant per instance.
- **Caution (finding):** the setup role *owns* the instance tables, so it can disable the ledger's guard triggers. It must be unlocked per action and never held by the always-running UI process.
- Not tested: other PostgreSQL versions, a Mac install (Postgres.app, Homebrew).

## Re-running the browser test
```bash
uv run python spikes/m8_spike_server.py
```
It prints a certificate path and one `security add-trusted-cert …` command **[U: macOS commands untested by me; Keychain Access works as a fallback: add the file to "login", open it, Trust > Always Trust]**. Open the three printed addresses, press **Show report**, then Ctrl-C and delete the certificate from Keychain Access ("Mini Perpetuity spike CA"). The authority can only vouch for `localhost` names and loopback addresses, and its private key is deleted right after the 7-day certificate is made.

MB18 on your own test PostgreSQL (superuser login; it creates and removes its own throwaway roles and instance):
```bash
MP_ADMIN_DSN='postgresql://YOU@localhost/postgres' uv run python spikes/mb18_roles.py
```

## What each open item changes
- **Safari, Firefox or Windows fails:** I propose another origin scheme before building, or the browser is listed as unsupported.
- **MB18 fails on Rio's PostgreSQL:** admin tasks stay in the terminal and the UI says so.
- **Windows:** the server does not run on Windows today (`config.py` imports the Unix-only `pwd` module, the password prompt reads `/dev/tty`, and file-safety checks use Unix permission bits). Supporting it is a separate step that needs a Windows test machine; Rio has not yet decided whether it is needed.
