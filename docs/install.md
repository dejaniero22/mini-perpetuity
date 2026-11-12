# Installing PostgreSQL 18 (Mac and Windows)

Mini Perpetuity needs **PostgreSQL 18** on the computer that runs it. This guide was written from the official pages listed under "Sources", read on 2026-11-12. Labels: **[V]** stated on an official page I read; **[U]** not verified (I could not install on a Mac or Windows machine from the authoring environment).

Use **version 18**, not a newer one. The project is tested on 18 only; I cannot confirm it works on 19 (a release candidate was announced on postgresql.org on 2026-10-15 **[V]**).

**Work computers:** installing a database server may need your employer's IT approval. Keep the `work` instance to synthetic data until the policy check (backlog MB6) is done.

## Mac: Postgres.app (recommended: no Terminal needed to install)
1. Open <https://postgresapp.com/downloads.html> and download **"Postgres.app with PostgreSQL 18"** (the "Latest Release"; at reading time v2.9.6, PostgreSQL 18.6, universal, macOS 10.15 or newer, 123 MB) **[V]**.
2. Move **Postgres.app** into the **Applications** folder (the site says some features may not work otherwise) **[V]**, then double-click it.
3. Click **Initialize** to create a server **[V]**.
4. The defaults are: host `localhost`, port `5432`, user = your Mac user name, database = same name, **no password** **[V]**. The connection URL is `postgresql://localhost` **[V]**.
5. The server runs only while the app is open (it lives in the menu bar; closing the app stops it) **[V, from the postgresql.org description]**.
6. Optional, to use the command-line tools (`psql`, `pg_dump`) anywhere: in Terminal run
   ```bash
   sudo mkdir -p /etc/paths.d && echo /Applications/Postgres.app/Contents/Versions/latest/bin | sudo tee /etc/paths.d/postgresapp
   ```
   then close Terminal and open a new window; `which psql` should print a path **[V]**.
7. For Mini Perpetuity, set `MP_ADMIN_DSN='postgresql://localhost/postgres'` when you run admin commands, and `pg_bin_dir = "/Applications/Postgres.app/Contents/Versions/latest/bin"` in an instance's config for backups (the folder comes from the PATH command above **[V]**; the exact DSN is **[Inference]**: libpq uses your OS user name when none is given, which I did not re-read in this session).

**What this means for security (backlog MB14). Do this before approving anything.** Postgres.app's own page says PostgreSQL "accepts connections from apps on your computer without a password" by default, called the "trust" method, and that Postgres.app 2.7 and later asks once per client app before letting it in <https://postgresapp.com/documentation/app-permissions.html> **[V]**. With trust, PostgreSQL accepts **any** password for the approver role, so the typed approver password would protect nothing. `mp doctor` detects this (it tries a wrong password) and `mp approve` refuses until it is fixed.

To fix it, make PostgreSQL check passwords for this project's roles only (your own superuser login keeps working as before):
1. As the admin run `psql -h localhost -c "SHOW hba_file;"` to find the login-rule file **[V: `hba_file` is a documented setting]**.
2. Open that file in a text editor and add these two lines **above every other `host` line** (the first matching line wins, with no fall-through **[V: PostgreSQL 18 documentation, `pg_hba.conf`]**; a user name starting with `/` is a regular expression, shown there for database names and tested here for user names **[V]**):
   ```
   host  all  /^mp_.*_(app|approver)$  127.0.0.1/32  scram-sha-256
   host  all  /^mp_.*_(app|approver)$  ::1/128       scram-sha-256
   ```
3. Reload: `psql -h localhost -c "SELECT pg_reload_conf();"` **[V]**. Then run `mp doctor`; "password login" should say ok.
I could not check where Postgres.app keeps this file or whether it overwrites edits on update **[U]**; `SHOW hba_file;` tells you the path, and `mp doctor` will tell you if it ever stops working.

Anything running as your Mac user can still act as the database superuser (that login stays passwordless). The approver design stops an AI or script that uses the everyday role, not software attacking your own Mac account.

Other Mac options exist (the EDB interactive installer, Homebrew `brew install postgresql@18`-style packages, MacPorts, Fink) **[V: listed on postgresql.org]**; I have not checked their steps. Do not install more than one.

## Windows: the EDB interactive installer
1. Open <https://www.postgresql.org/download/windows/> and use "Download the installer" (hosted by EDB). Choose **version 18**. The page says 18 is tested on Windows Server 2022 and 2025 and generally runs on comparable desktop Windows **[V]**.
2. The installer includes the server, pgAdmin and Stack Builder; it runs graphically **[V]**. Stack Builder is optional; you do not need it.
3. **[U]** During setup it asks for a password for the database superuser (`postgres`), a port (default 5432) and an install folder. I could not read the vendor's Windows instructions this session (the page I needed was not reachable), so these details and the location of `pg_dump.exe` are unverified. Use a strong password and store it in your password manager; Mini Perpetuity only uses it for admin commands.
4. **Windows support in Mini Perpetuity does not exist yet.** The server code does not run on Windows today (it needs a separate port, planned as the next step). Install PostgreSQL on Windows only when that step is done, unless you want to prepare early.

## After installing: check it works
Open Terminal (Mac) and run `psql --version` (needs the PATH step above) or just confirm the Postgres.app window shows a running server. Then continue with the README quick start.

## Sources (all read 2026-11-12)
- macOS packages: <https://www.postgresql.org/download/macosx/>
- Windows installers: <https://www.postgresql.org/download/windows/>
- Postgres.app: <https://postgresapp.com/>, <https://postgresapp.com/downloads.html>, <https://postgresapp.com/documentation/cli-tools.html>
- EDB installer downloads (lists 18.x for Windows): <https://www.enterprisedb.com/downloads/postgres-postgresql-downloads>
