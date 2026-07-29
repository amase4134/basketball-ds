#!/bin/bash

# Cursor hooks receive event details on stdin. Consume them so the hook can
# evolve without leaving unread input attached to the process.
cat >/dev/null

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo '{}'
  exit 0
fi

if git diff --quiet &&
  git diff --cached --quiet &&
  [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
  echo '{}'
  exit 0
fi

git add -A >/dev/null 2>&1

if git diff --cached --quiet; then
  echo '{}'
  exit 0
fi

# Keep hook output valid JSON; Git writes any commit diagnostics to stderr.
if ! git commit -m "chore: auto-commit agent changes" >/dev/null; then
  echo "Cursor auto-commit hook: git commit failed" >&2
fi

echo '{}'
