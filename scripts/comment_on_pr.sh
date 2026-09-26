#!/usr/bin/env bash
# Comment a markdown report on the pull request this run belongs to.
#
# Used by every measurement workflow. The old hardcoded `gh pr comment 1` worked
# only for the first PR; this resolves the right one on both `pull_request` and
# `push` events, and never fails the job because of a comment.
#
# Usage: bash scripts/comment_on_pr.sh /tmp/report.md
set -uo pipefail

BODY="${1:-/tmp/report.md}"
if [ ! -s "$BODY" ]; then
  echo "comment_on_pr: no report at ${BODY}"
  exit 0
fi

PR=""
if [ -n "${GITHUB_EVENT_PATH:-}" ] && [ -f "${GITHUB_EVENT_PATH:-}" ]; then
  PR=$(python3 - <<'PY'
import json, os
try:
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    print((event.get("pull_request") or {}).get("number") or "")
except Exception:
    print("")
PY
)
fi

if [ -z "$PR" ]; then
  BRANCH="${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-}}"
  OWNER="${GITHUB_REPOSITORY%%/*}"
  PR=$(gh api "repos/${GITHUB_REPOSITORY}/pulls?head=${OWNER}:${BRANCH}&state=all" \
    --jq '.[0].number // empty' 2>/dev/null || true)
fi

if [ -z "$PR" ]; then
  PR=$(gh pr list --state open --limit 1 --json number --jq '.[0].number // empty' 2>/dev/null || true)
fi

if [ -z "$PR" ]; then
  echo "comment_on_pr: no pull request found for ${GITHUB_REF_NAME:-this ref}"
  exit 0
fi

if gh pr comment "$PR" --body-file "$BODY"; then
  echo "comment_on_pr: commented on PR #${PR}"
else
  echo "comment_on_pr: could not comment on PR #${PR} (not fatal)"
fi
exit 0
