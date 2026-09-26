#!/usr/bin/env bash
# One key, three roles, checked against the live APIs.
#
# Usage: bash scripts/single_key_check.sh nvidia|groq
#
# Runs the same self-test the sidebar runs, with only that provider's key in the environment
# and every role/model override removed, so the derivation (providers.derive_selection) has to
# supply the planner, the executor and the text writer on its own. Prints the report and fails
# on a role that cannot run; a role that is merely rate-limited right now is a warning, not a
# failure, because that is capacity and not configuration.
set -uo pipefail

KEY="${1:-}"
case "$KEY" in
  nvidia) VARIABLE="NVIDIA_API_KEY" ;;
  groq)   VARIABLE="GROQ_API_KEY" ;;
  *) echo "usage: $0 nvidia|groq" >&2; exit 2 ;;
esac

if [ -z "${!VARIABLE:-}" ]; then
  echo "No $VARIABLE secret configured, so the $KEY-only setup cannot be checked here."
  echo "Add it under Settings -> Secrets and variables -> Actions to enable this job."
  exit 0
fi

# Nothing but the one key: an override left in the environment would hide a broken derivation.
unset PLANNER_PROVIDER PLANNER_MODEL PLANNER_BASE_URL PLANNER_API_KEY PLANNER_KEY \
      POLICY_PROVIDER POLICY_MODEL POLICY_BASE_URL POLICY_API_KEY POLICY_KEY \
      TEXT_MODEL_PROVIDER TEXT_MODEL TEXT_BASE_URL TEXT_MODEL_API_KEY TEXT_MODEL_KEY \
      TYPESAFE_API_KEY JEV_ENV_FILE 2>/dev/null || true

REPORT="$(mktemp)"
python scripts/check_providers.py | tee "$REPORT"
status=$?

echo
echo "### Which models one $KEY key chose"
echo
echo "| Role | Model |"
echo "| --- | --- |"
grep -E '^\| (planner|policy|text) ' "$REPORT" | sed 's/^| \([a-z]*\) | `\(.*\)` |.*/| \1 | `\2` |/'

red=$(grep -c '🔴' "$REPORT" 2>/dev/null) || true
limited=$(grep -c '🟡' "$REPORT" 2>/dev/null) || true
red=${red:-0}
limited=${limited:-0}
echo
if [ "$status" -ne 0 ]; then
  echo "The self-test itself failed (exit $status); see the report above."
  exit 1
fi
if [ "$red" -gt 0 ]; then
  echo "FAILED: $red role(s) cannot run with this one key. That contradicts the one-key setup."
  exit 1
fi
if [ "$limited" -gt 0 ]; then
  echo "All three roles answered; $limited is rate-limited at this moment (capacity, not configuration)."
else
  echo "All three roles ran from the single $KEY key."
fi
