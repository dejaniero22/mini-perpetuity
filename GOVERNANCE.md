# Governance (subset)

This repo adopts a **subset** of the main Perpetuity Machine Guide (`docs/governance/GOVERNING_GUIDE.md` in the main repo): its priority order, evidence labels and the invariants listed in plan §5. The main Guide was not available when this file was written, so its text is not reproduced here; read it there. Section numbers cited from it (e.g. §3, §7.2) come from the build plan and are **[U]** until re-checked.

## Evidence labels
**[V]** checked against a primary source · **[V2]** secondary source only · **[U]** not verified · **[Inference]** reasoned judgment.

## Invariants kept
INV-1, 2, 3, 4, 6, 7, 9, 10, 11, 12, 13. INV-8 deferred. Details: plan §5.

## Deviations from the main repo
| # | Deviation | Record |
|---|---|---|
| D1 | INV-5 (authorization service) replaced by instance boundary + one default-deny `authorize()` function | ADR-M1 |
| D2 | INV-13 audit simplified: events in the ledger only | plan §5 |
| D3 | INV-3 provenance simplified to three PROV-O-style columns | plan §5 |
| D4 | Separate repo; main phase gates unaffected. Nothing moves to main by copying; only via main's ADR/PR process | ADR-M0 |
| D6 | Any number of named instances instead of exactly `personal` and `work` (plan §1, §4.1, M1 assume two) | ADR-M1 (amended) |
| D7 | A local HTTPS web front end (plan §1 lists HTTP as out of scope; remote access stays out of scope) | ADR-M8 |
| D5 | `.claude/settings.json` and `protect-paths.sh` are written fresh, not copied (main repo unavailable at M0) | `docs/learning/M0.md` |

## Process

- **One pull request per plan step** (M0, M1, M2, …). A PR contains that step only; the next step is not started in the same PR.
- **Branches:** `main` is the default and only long-lived branch. One short-lived branch per step, named `m<N>-<slug>` (for example `m1-instances`). The branch is deleted when its PR merges.
- **Stacking:** if a step is built before the previous PR merges, its PR targets the previous step's branch and is retargeted to `main` when that PR merges. Never merge a later step before an earlier one.
- **Commits:** one commit per step, kept clean before review (no fix-up commits in the history). Merge with squash or rebase so `main` stays linear.
- **Each PR includes:** tests including negative tests, Ruff, mypy `--strict`, pytest, gitleaks, pip-audit once dependencies exist, a CHANGELOG entry, and a short Learning Note in `docs/learning/`.
- **Exit:** a step is done when its exit criteria (plan §6) pass in CI and Rio accepts it. Nothing starts before the previous step is accepted.
- **Protected paths:** `.claude/settings.json`, `.claude/hooks/`, `.github/workflows/` change only with Rio's explicit say-so (a hook enforces this for the AI).
