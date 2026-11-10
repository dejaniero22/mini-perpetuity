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
| D5 | `.claude/settings.json` and `protect-paths.sh` are written fresh, not copied (main repo unavailable at M0) | `docs/learning/M0.md` |

## Process
One small PR per step. Each: tests incl. negative tests, Ruff, mypy `--strict`, pytest, gitleaks, pip-audit once dependencies exist, CHANGELOG entry, short Learning Note in `docs/learning/`.
