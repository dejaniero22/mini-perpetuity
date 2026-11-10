#!/usr/bin/env bash
# Run: bash tests/hooks/test_protect_paths.sh
hook="$(dirname "$0")/../../.claude/hooks/protect-paths.sh"
fail=0
check() { # expected_exit path
  printf '{"tool_input":{"file_path":"%s"}}' "$2" | bash "$hook" 2>/dev/null; got=$?
  [ "$got" -eq "$1" ] || { echo "FAIL: $2 expected $1 got $got"; fail=1; }
}
for p in /r/.claude/settings.json /r/.claude/hooks/protect-paths.sh /r/.github/workflows/ci.yml /r/.env /r/.env.local; do check 2 "$p"; done
for p in /r/README.md /r/src/app.py /r/docs/envelope.md; do check 0 "$p"; done
printf '{}' | bash "$hook"; [ $? -eq 0 ] || { echo "FAIL: empty input"; fail=1; }
[ $fail -eq 0 ] && echo "protect-paths tests: ok"; exit $fail
