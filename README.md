# mini-perpetuity

Single-user, local memory layer with two isolated instances (`personal`, `work`). Build plan: [docs/plan/BUILD_PLAN.md](docs/plan/BUILD_PLAN.md). Status: **M0 (foundations, no product code)**.

## Client rule (plan §4.2)
Register the `work` MCP server only in a work-only Claude Project/profile, and `personal` only in a personal one. No database control stops a client that has both enabled from mixing them.

## Data rules (plan §3)
No real data in git, ever. `work` uses synthetic content only until the employer AI/data-handling policy is checked (backlog MB6). No employee records, HRIS data, or medical/leave/compensation details in either instance.

See [GOVERNANCE.md](GOVERNANCE.md), [SECURITY.md](SECURITY.md), [docs/verification-backlog.md](docs/verification-backlog.md).
