# ADR-M6: Write-back with quarantine

- **Status:** Accepted. Decisions 1 and 2 were given by Rio on 2026-11-11. **Amended by [ADR-M6b](ADR-M6b-approver-role.md):** the limit below ("the database cannot tell your CLI from a shell") is addressed there by a separate approver role.
- **Date:** 2026-11-11

## Decisions (Rio, 2026-11-11)
1. A client may propose two types, **`ai_inference` or `summary`**, and **must give a source pointer** (`derived_from`: memory ids or a document reference; max 500 characters).
2. A proposal's text is capped at **2,000 characters**, so a runaway client cannot flood the review queue with huge items.

## Design
- **`propose_memory`** (MCP tool) always creates `status = proposed`, with the calling client recorded as the agent (`client:<name>`) and the activity `mcp:propose_memory`. The tool has no parameter for status, sensitivity, validity or author. A proposal is invisible to search and `get_memory`, in every mode, for every principal, until the owner approves it.
- **`mp review`, `mp approve ID`, `mp reject ID --reason TEXT`** are the owner's CLI commands. Only the owner principal is allowed these actions; no client principal is.
- **Interpretation (Rio to confirm):** the plan says approve and reject "record a `human_decision` event". I read that as a ledger event with activity `human_decision` and payload `{decision, id, reason, proposed_by}`. A `human_decision` *memory object* is not created. **[Inference]**
- **Two layers of enforcement.** In code, `propose.py` can only create proposals and `review.py` holds the only approval path; the MCP server never imports `review.py` (a test runs the server module in a fresh interpreter and checks). In the database (migration 0006): a trigger refuses any INSERT attributed to a `client:` agent unless it is a `proposed` `ai_inference` or `summary` with no `supersedes`, and the update guard now allows exactly two changes: expiring a live approved version (corrections) and deciding a pending proposal (`proposed` to `approved` or `rejected`).
- **Beyond the decisions, mine (easy to change):** a client may have at most **50 proposals** waiting (a size cap alone does not stop many small ones), and proposals count toward the same per-minute request quota as reads. `mp review` is itself audited.
- **Approval does not launder authorship:** an approved proposal stays `ai_inference` or `summary`, attributed to the client, with its source pointer.

## Limits (read these)
- **The database cannot tell your CLI from a shell.** The app role has to be able to update `status` for `mp approve` to work, and the CLI, the MCP server and any shell all use that same role. So a client that can run commands as your OS user could approve its own proposal with plain SQL (it can read the `0600` password file; see ADR-M5). The quarantine protects against what clients can do *through the MCP server*, and against bugs in the server. **[Inference]** If you want approval to hold even against a shell-capable client, the fix is a separate approver role whose password you type at `mp approve` and never store; that is not built.
- An approved proposal's `recorded_at` is when it was **proposed**; the decision time is in the ledger (`human_decision`).
- Proposals are always `normal` sensitivity. If a proposal is really sensitive, reject it and add the note yourself with `mp add --sensitivity sensitive`.
- The review listing prints AI-written text. It is sanitized (control characters replaced) and every content line is prefixed with `| `, so stored text cannot impersonate the tool's own output. It cannot stop an AI from writing a misleading but well-formed note: read proposals before approving.
- Rate-limited requests and proposals past the queue cap are refused without a ledger entry.
