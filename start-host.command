#!/bin/bash
# Starter for the AI Agent for Firefox bridge host (macOS).
# First time: right-click -> Open -> Open (Gatekeeper asks once).
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

if [ ! -d ".venv" ]; then
  echo ""
  echo "First run: preparing the agent. About one minute, internet needed..."
  if ! "$PY" -m venv .venv; then
    echo ""
    echo " [!] Could not create the private environment (the 'venv' module is missing)."
    echo "     On $(os_name), fix it with:"
    echo "       $(venv_hint)"
    echo "     Then run this file again."
    echo ""
  read -n 1 -s -r -p "Press any key to close..."
  echo ""
    exit 1
  fi
  .venv/bin/python -m pip install --quiet --upgrade pip
  if ! .venv/bin/python -m pip install --quiet -e ".[documents]"; then
    echo ""
    echo " [!] Installation failed. Check your internet connection and run this file again."
    echo ""
  read -n 1 -s -r -p "Press any key to close..."
  echo ""
    exit 1
  fi
fi

# Earlier templates shipped a wrong NVIDIA model id (zai/ instead of z-ai/): fix it in place.
if [ -f ".env" ] && grep -q "zai/glm-5.3" .env; then
  sed -i.bak 's|zai/glm-5\.3|z-ai/glm-5.3|g' .env
  echo "Fixed an outdated model id in .env (zai/ -> z-ai/). Backup saved as .env.bak"
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

echo ""
echo "Registering the host with Firefox..."
if .venv/bin/jev-register-host >/dev/null 2>&1; then
  echo "  Done: from now on the sidebar starts the agent by itself - you will not need this window again."
  echo "  This one stays open only as a fallback: if the panel says offline, it is to blame."
  echo ""
else
  echo "  [!] Could not register it (Firefox will not start the agent on its own)."
  echo "      Keep this window open while you use the agent - it still works exactly the same."
  echo ""
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
