# CLAUDE.md

Mini Perpetuity: single-user memory layer, N isolated named instances (separate accounts). Plan: `docs/plan/BUILD_PLAN.md`. Rules: `GOVERNANCE.md`.

- Work one milestone at a time (M0, M1, …). Do not start step N+1 before N's exit criteria are met and Rio accepts it.
- Label claims **[V]/[V2]/[U]/[Inference]**. Say "I cannot confirm this" instead of guessing. Check package names, versions and licenses against a primary source before adding a dependency.
- Never put real personal/work data, secrets or dumps in the repo. Synthetic fixtures only.
- The AI never approves its own memory proposals (INV-7/12).
- Every step: tests incl. negative tests, Ruff, mypy `--strict`, pytest, gitleaks, CHANGELOG entry, Learning Note.
- Do not edit `.claude/settings.json`, `.claude/hooks/`, or `.github/workflows/` without Rio's explicit say-so (a hook enforces this).
