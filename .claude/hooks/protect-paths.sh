#!/usr/bin/env bash
# PreToolUse hook: block edits to guard files. Reads tool-call JSON on stdin; exit 2 blocks.
p=$(jq -r '.tool_input.file_path // empty')
case "$p" in
  */.claude/settings.json|*/.claude/hooks/*|*/.github/workflows/*|*/.env|*/.env.*)
    echo "protect-paths: '$p' is protected; ask Rio to edit it." >&2; exit 2 ;;
esac
exit 0
