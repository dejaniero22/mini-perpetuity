# ADR-M6b: A separate approver role (separation of duties)

- **Status:** Proposed. Requested by Rio on 2026-11-12; accepted when its PR merges.
- **Date:** 2026-11-12
- **Amends:** [ADR-M6](ADR-M6-write-back.md), whose main limit this addresses.

## Problem
Until now the MCP server, `mp search`, `mp add`, `mp approve` and any shell used the same database role. So anything that could run commands as your OS user could approve its own proposal (`mp approve 7`, or plain SQL) or create "approved" memory directly (`mp add`). Making only `mp approve` stricter would not help while `mp add` stayed open.

## Decisions (Rio, 2026-11-12)
1. Build a stronger approval design before M7.
2. **"Add freely, approve in batches":** `mp add` asks for nothing and stores a **pending** note; one typed password approves a batch. (Rio asked for something "more dynamic" than a password per `mp add`, and chose this from four options. The other options were: a time-boxed unlock; a Touch ID or OS prompt; a password for every action.)
3. On the question of accepting the limits below, Rio had no preference; the design proceeds with the limits documented and is open to a harder boundary later.

## Design
Two database roles per instance, enforced by the database, not by application code:

| | **everyday role** `mp_<name>_app` | **approver role** `mp_<name>_approver` |
|---|---|---|
| Password | stored in a `0600` file; used by the MCP server, `search`, `review`, `status`, `add` | **typed by you, stored nowhere**; the database keeps only a SCRAM-SHA-256 hash |
| Read memory and ledger | yes | yes |
| Create memory | **pending only** (a trigger refuses any other status) | any status (needed for corrections) |
| Change a status (approve, reject, supersede) | **no** (the privilege is revoked, and a trigger refuses it) | yes (the two allowed transitions only) |
| Write `human_decision` and `memory.correct` ledger events | **no** (a trigger refuses it) | yes |
| Write other audit events | yes | yes |

- **Commands.** `mp add` stores a pending note as the everyday role. `mp review` lists pending items (yours and AI proposals). `mp approve` and `mp reject` take several ids or `--all`, print exactly what will be decided (sanitized, each content line prefixed with `| `), ask for the password **once**, and decide them all in one transaction (all or none). Nothing is searchable until approved.
- **Setting the password:** `mp admin set-approver NAME` (admin connection; typed twice; at least 16 characters and not repetitive). The password is hashed on the client with SCRAM-SHA-256 and only the hash is sent, so it never appears in a SQL statement or server log; a test checks this. Until it is set, the approver cannot log in and `mp approve` says so before asking for anything.
- **Terminal only.** The password is read from a terminal and nowhere else: not an argument, an environment variable, a file or a pipe. With no terminal at all (a script, an AI's shell tool) the command refuses. An empty entry is refused, because libpq would otherwise fall back to `PGPASSWORD` or `~/.pgpass`.
- **No "approve all of mine".** The author label on a pending item is just text that anything with the everyday role could write, so a shortcut keyed on it would make the password a rubber stamp. You see each item before you approve it.
- **Upgrade:** `mp admin migrate NAME` applies migration 0007 to an existing instance, which creates the approver role (cannot log in yet) and removes the everyday role's update privilege. Run `mp admin set-approver NAME` next. Existing data is untouched. Until the approver is set, pending items wait.
- Superusers (the admin connection) pass the database checks, because they can disable triggers anyway.

## What this protects. **[Inference]**
- An AI client using the MCP server, and any bug in the server code, cannot make anything trusted.
- An AI that only *uses* what you can use (runs `mp add`, runs `psql`, reads your config and password files) cannot approve, cannot create approved memory, cannot supersede or hide approved memory, and cannot forge a `human_decision` event. It can still propose, and still read.

## What it does not protect. **Read this.**
- **Active tampering by something running as your OS user.** It could edit the `mp` code or your shell startup files or `PATH` to capture the password the next time you type it, or use keystroke injection. Where PostgreSQL runs as your user (Postgres.app, Homebrew), it could also change the server's configuration or data files (for example to disable password checks). A real boundary against that needs the approval step, or PostgreSQL, under a **different OS user or on another machine**. Not built.
- **Confidentiality.** The everyday role can still read everything in an instance, including `sensitive` objects; the sensitivity rule only constrains the MCP server.
- **Audit entries by the everyday role** (such as `memory.search`) are not authenticated: anything holding it can write one claiming any actor. The two *trusted* kinds (`human_decision`, `memory.correct`) are protected.
- **Guessing the password.** An online guesser with the everyday role could try approver passwords against the database; PostgreSQL has no built-in lockout. Hence the length rule. Failed logins appear in the PostgreSQL log, not in the ledger. A long passphrase is the defence.
- **Whoever you tell.** The password is only as safe as you are with it (do not paste it into a chat with an AI client).

## Rejected or deferred
- **Password for every `mp add`:** strictest and simplest, but the friction Rio did not want.
- **Time-boxed unlock (sudo style):** during the window anything running as you could add or approve; it would also need somewhere to keep the unlocked credential.
- **Touch ID or an OS prompt:** a program cannot tap for you, which is attractive, but it cannot be built or tested on the Linux build sandbox. Deferred; backlog MB15.
- **A password for approvals only (not `mp add`):** `mp add` would still let anything running as you create approved memory.
