#!/usr/bin/env bash
# Manual installer for the local decision engine (Laya).
#
# It is no longer something you have to remember: the agent installs it by itself on the
# first normal start. This script exists for the cases where that did not happen — an
# offline first start, a machine where the download failed, or a deliberate reinstall.
# Both paths run the same code, so the messages and the CPU-torch choice are identical.
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "First run the main starter (start-host.sh) once, then try again."
  exit 1
fi
exec .venv/bin/python -m jev_ultrafast.laya_install
