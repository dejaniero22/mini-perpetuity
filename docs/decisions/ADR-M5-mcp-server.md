# ADR-M5: MCP server, client principals, and the SDK

- **Status:** Proposed. Decisions 1 to 3 below were given by Rio on 2026-11-11; the ADR is accepted when its PR merges.
- **Date:** 2026-11-11

## Decisions (Rio, 2026-11-11)
1. **Each AI client gets its own allowed principal.** A client is registered by name in the instance config (`clients = ["claude-desktop"]`) and acts as `client:<name>`. The owner stays `human:<OS user>`. An unregistered client cannot even start the server.
2. **Sensitive objects are never returned to an AI client.** The rule lives in the retrieval layer (`search_memory`, `get_memory`), not in the MCP layer, so no caller can bypass it. The owner (CLI) still sees them. A hidden object is indistinguishable from a missing one.
3. **Rate limiting is on.** Counted from the ledger itself, so it survives restarts: a registered client is limited to 60 requests per minute; denied requests are limited to 20 per minute per instance, after which they are still refused but no longer written to the ledger. The owner is exempt from the request limit.

## The SDK (plan §6 required these facts before installing). **[V]**
Sources: PyPI JSON for `mcp`, and the SDK repository at tag `v2.3.0` (LICENSE, README, `docs/`), read 2026-11-11.

| Item | Finding |
|---|---|
| Package | `mcp` (official; repository `modelcontextprotocol/python-sdk`) |
| Version | 2.3.0, released 2026-10-02; pinned exactly in `pyproject.toml` |
| License | MIT (PyPI metadata and the repository `LICENSE`) |
| Python | `requires-python >=3.10`; Python 3.14 classifier present; imported and used on 3.14.6 here |
| API | 2.x: `mcp.server.MCPServer` and `ToolError` (the 1.x `FastMCP` name is not used) |
| Footprint | 27 transitive packages (including `pydantic`, `starlette`, `uvicorn`, `cryptography`); the stdio server needs only part of them. `pip-audit`: no known vulnerabilities at install |

Risk: a new major version, so its API may change; the exact pin and the contract tests (which use the SDK's own client over real stdio) are the guard.

## Design
- **One process = one instance + one client**, fixed at launch: `mp serve --client <name> --config <file>`. No tool takes an instance, a config or a principal.
- **Exactly two tools**, `search_memory` and `get_memory`, both read-only. No resources, no prompts.
- **Output is marked untrusted.** Results are returned between a random-nonce boundary tag with `<`, `>` and `&` escaped inside the JSON, preceded by a fixed notice. Stored text can neither forge nor close the boundary. Fields are truncated at 4000 characters and flagged.
- **Errors**: anticipated failures (denied, rate limited, bad input, not found) are `ToolError`s with a short message; anything unexpected becomes a generic error and is logged to stderr, never to stdout (stdout carries the protocol).

## Limits (read these)
- **A stdio server cannot verify who launched it.** `--client` is a label chosen in the host's config, as trustworthy as that config file. It separates clients in the audit trail and the quotas; it does not authenticate them. **[Inference]** The MCP specification says stdio servers should take credentials from the environment rather than use its OAuth flow (plan §4.3, cited there).
- **An AI client that can run shell commands as your OS user can bypass the MCP server entirely:** it can read the `0600` password file and connect to PostgreSQL as the app role, which can read everything in that instance (but not edit the ledger or memory history). Registering a client in this system only constrains what it can do *through the server*. Use it with clients that have no shell access as you, or give such a client no access to the config directory.
- **Quotas are not atomic**: count-then-insert can overshoot slightly under concurrent calls.
- Rate-limited requests are refused without a ledger entry (by design, so the ledger cannot be flooded), so a flood is visible only through the client's own error messages.
- The tools set no MCP annotations (such as a read-only hint), so hosts may ask for approval on every call. A search does append to the audit ledger, so calling the tools strictly "read-only" would overstate it.

## Rejected
- A hand-written JSON-RPC server: the plan names the official SDK.
- Environment-variable-only configuration for the server: host support for passing environment variables varies, so `--config` is accepted as well (`MP_CONFIG` still works).
