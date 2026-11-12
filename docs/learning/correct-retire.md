# Learning Note: correct and retire

- **A memory you cannot fix is a liability.** Approving a note made it permanent: a typo stayed searchable and was served to AI clients. The database already supported corrections; no command used them. The lesson from the audit: a capability that tests exercise but no user can reach is unfinished.
- **Reuse the trusted path.** Changing approved memory needs the same approver password as approving. The code reuses the approval login, the "password must really be checked" refusal, and the all-or-nothing transaction. Two small helpers were split out of the approve command instead of copying it.
- **Retire is a correction, not a delete.** The table forbids editing a row, so "retire" writes a new version whose end-of-truth time is now. Normal search then skips it; history still shows both versions and why.
- **A found bug: future-dated notes.** The first version looked notes up with the normal search rule (valid now), so a note dated for next week could not be retired. A dedicated lookup (approved, not replaced, not already ended) fixed it, and a test covers it.
- **Two layers of input checks.** The command checks reason, text and the secret/ID seatbelt before it asks for a password (so a typo costs nothing), and the library function checks them again (so another caller cannot skip them). Mutation checks showed the library-level checks were untested; tests were added.
- **An equivalent change.** Removing "not replaced" from the lookup changed nothing, because a replaced note is already not "approved". It was left in for clarity.
- **Not verified.** Nothing on a Mac or Windows yet.
