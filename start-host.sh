#!/bin/bash
# Starter for the AI Agent for Firefox bridge host (Linux).
# Run it from your file manager by double-clicking this file.
# Double-click it, or run it from a terminal.

# ── find a Python the agent can run on ───────────────────────────────────────
# Distro Python names vary (openSUSE ships python3.11/3.12/3.13 side by side,
# Debian separates python3-venv, Fedora has python3.12, Arch is always current):
# look for any interpreter new enough instead of assuming `python3` is the one.
REQUIRED_MAJOR=3
REQUIRED_MINOR=11

python_ok() {
  command -v "$1" >/dev/null 2>&1 || return 1
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= ($REQUIRED_MAJOR, $REQUIRED_MINOR) else 1)" >/dev/null 2>&1
}

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if python_ok "$candidate"; then printf '%s' "$candidate"; return 0; fi
  done
  return 1
}

os_name() {  # "SUSE", "Debian", "Fedora", "Arch", "macOS", "other"
  if [ "$(uname -s)" = "Darwin" ]; then printf 'macOS'; return; fi
  local release="${JEV_OS_RELEASE:-/etc/os-release}" id=""
  if [ -r "$release" ]; then
    id=$( ( . "$release" 2>/dev/null || true; printf '%s %s' "${ID:-}" "${ID_LIKE:-}" ) )
  fi
  case " $id " in
    *suse*)                   printf 'SUSE' ;;
    *debian*|*ubuntu*)        printf 'Debian' ;;
    *fedora*|*rhel*|*centos*) printf 'Fedora' ;;
    *arch*)                   printf 'Arch' ;;
    *)                        printf 'other' ;;
  esac
}

install_hint() {  # the exact command for this system
  case "$(os_name)" in
    SUSE)   printf 'sudo zypper install python312 python312-pip' ;;
    Debian) printf 'sudo apt update && sudo apt install python3 python3-pip python3-venv' ;;
    Fedora) printf 'sudo dnf install python3.12 python3-pip' ;;
    Arch)   printf 'sudo pacman -S python' ;;
    macOS)  printf 'brew install python@3.12   (or download it from https://www.python.org/downloads/)' ;;
    *)      printf 'Download it from https://www.python.org/downloads/' ;;
  esac
}

venv_hint() {  # the venv module ships separately on some systems
  case "$(os_name)" in
    SUSE)   printf 'sudo zypper install python312 python312-pip' ;;
    Debian) printf 'sudo apt install python3-venv' ;;
    Fedora) printf 'sudo dnf install python3-libs' ;;
    Arch)   printf 'sudo pacman -S python' ;;
    macOS)  printf 'brew install python@3.12' ;;
    *)      printf 'install the package that provides the venv module' ;;
  esac
}

PY=$(find_python)
if [ -z "$PY" ]; then
  echo ""
  echo " [!] Python $REQUIRED_MAJOR.$REQUIRED_MINOR or newer is required, and this system does not have one."
  if command -v python3 >/dev/null 2>&1; then
    echo "     (Found: $(python3 --version 2>&1) - too old.)"
  fi
  echo ""
  echo "     For $(os_name), the command is:"
  echo "       $(install_hint)"
  echo ""
  echo "     Then run this file again. Nothing else is needed."
  echo ""
  exit 1
fi
if [ -n "${JEV_START_DRY_RUN:-}" ]; then echo "PYTHON=$PY"; exit 0; fi

# ── install or update ────────────────────────────────────────────────────────
# One place decides what to install: scripts/install.py. Double-clicking always checks, so a
# newer copy of the agent replaces the previous installation instead of being layered on top,
# and an existing Laya is never installed a second time. Your keys (.env), your run history
# (artifacts/) and your files (workspace/) are never touched by it.
echo ""
echo "Checking the installation..."
if ! "$PY" scripts/install.py; then
  echo ""
  echo " [!] The agent could not be installed or updated."
  echo "     If it is the 'venv' module, on $(os_name) it comes with:"
  echo "       $(venv_hint)"
  echo "     Otherwise check your internet connection, then run this file again."
  echo "     Your keys and your files were not touched."
  echo ""
  exit 1
fi

# Two lines in .env can pin the model that the agent used to derive: the wrong id some old
# templates shipped (zai/glm-5.3) and the model itself (z-ai/glm-5.3). Both move to the model
# this version ships, z-ai/glm-5.3-flash, and the change is printed. JEV_KEEP_MODEL=1 skips it
# for anyone who deliberately wants the full-size model.
if [ -f ".env" ] && [ "${JEV_KEEP_MODEL:-0}" != "1" ] && grep -qE "^[A-Z_]*MODEL=(zai|z-ai)/glm-5\.3[[:space:]]*$" .env; then
  sed -i.bak -E 's#^([A-Z_]*MODEL=)(zai|z-ai)/glm-5\.3[[:space:]]*$#\1z-ai/glm-5.3-flash#' .env
  echo "Updated the pinned model in .env to z-ai/glm-5.3-flash (thinking off is already the default)."
  echo "Backup saved as .env.bak. Keep the old model with JEV_KEEP_MODEL=1."
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

echo "Host starting. KEEP THIS WINDOW OPEN while you use the sidebar."
echo ""
echo "Paste your free API key in the sidebar - it will ask (2 minutes at https://build.nvidia.com)."
echo ""
echo "Now in Firefox:"
echo "  1. Type about:debugging in the address bar and press Enter"
echo "  2. Click 'This Firefox' then 'Load Temporary Add-on...'"
echo "  3. Open this folder, then the 'extension' folder, pick 'manifest.json'"
echo "  4. Open the AI Agent sidebar with the toolbar button"
echo ""
exec .venv/bin/jev-firefox
