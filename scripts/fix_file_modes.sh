#!/usr/bin/env bash
# Put the executable bit back on the double-click starters.
#
# Why this exists: git keeps the mode in the index, some editors, ZIP round-trips and several
# sandboxes rewrite the file and drop it, and `git add -A` then stages that as a change — which
# `tests/test_platform.py` catches in CI, on every job, as "these starters lost their executable
# bit". Run this before committing if you touched those files.
set -e
cd "$(dirname "$0")/.."
chmod +x start-host.sh start-host.command install-laya.sh install-laya.command scripts/*.sh 2>/dev/null || true
git update-index --chmod=+x start-host.sh start-host.command install-laya.sh install-laya.command scripts/*.sh
git ls-files -s start-host.sh start-host.command install-laya.sh install-laya.command | awk '{print $1, $4}'
