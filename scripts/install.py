#!/usr/bin/env python3
"""The smart installer: keep what is current, replace what is stale, never touch your data.

The starters used to ask one question — "does `.venv` exist?" — and if it did, they never
looked again. That leaves a machine frozen on the first version it installed: new dependencies
never arrive, a virtualenv built by an older Python keeps being used, and a half-finished
install is indistinguishable from a finished one.

What this does instead:

* **Current** environment (same shipped version, same Python line, entry points present) → reuse
  it. Nothing is downloaded, nothing is touched.
* **Stale or broken** → remove the environment and install this copy's version, printing the
  version it found and the one it installs. That includes the case the user asked about: an
  install that exists but is old is replaced, not patched on top of.
* **`--reinstall`** → do that even when it looks current, for a clean start.

What it never removes, because it is not the installation, it is the user's: `.env` (the API
keys), `artifacts/` (run history and recordings), `workspace/` (files the agent made), `.git/`,
the extension, and the shared caches outside the project — the Laya weights and pip's download
cache. Those caches are exactly why a rebuild does not cost 1.3 GB again.

Laya itself is remembered: if the environment being replaced had it, the new one gets the
package back (the weights are already on disk, so nothing large is downloaded), and if it did
not, nothing is added here — the agent installs it in the background on the first start, as it
always does.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_PYTHON = (3, 11)
LAYA_REQUIREMENT = "laya>=0.3,<1"
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
TORCH_PLATFORMS_USING_CPU_INDEX = ("linux", "win32")
LOCAL_DATA = ("laya-install.json",)  # next to .env: the record of an earlier Laya install

# Nothing below is ever deleted by this script. Kept as data rather than as prose so a mistake
# fails a test instead of a user's afternoon.
NEVER_REMOVE = (
    ".env",
    ".env.bak",
    ".env.example",
    "artifacts",
    "workspace",
    ".git",
    "extension",
    "docs",
    "scripts",
    "skills",
    "tests",
    "CHANGELOG.md",
    "README.md",
)


# ── reading the state of a machine ───────────────────────────────────────────


def shipped_version(root):
    """The version this copy of the project installs, from pyproject.toml."""
    text = (Path(root) / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml has no version: this copy is incomplete")
    return match.group(1)


def venv_dir(root):
    return Path(root) / ".venv"


def venv_python(venv):
    """The interpreter inside the environment, on any platform."""
    for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return None


def venv_python_version(venv):
    """'3.13.5' as the environment recorded it when it was created."""
    try:
        text = (Path(venv) / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^version\s*=\s*([0-9.]+)", text, re.MULTILINE)
    return match.group(1) if match else None


def site_packages(venv):
    """Every site-packages directory in the environment (there is normally one)."""
    found = []
    for pattern in ("lib/python*/site-packages", "Lib/site-packages"):
        found.extend(sorted(Path(venv).glob(pattern)))
    return found


def installed_version(venv):
    """The version of this package installed in the environment, from its metadata directory."""
    versions = []
    for packages in site_packages(venv):
        for info in packages.glob("jev_ultrafast-*.dist-info"):
            versions.append(info.name[len("jev_ultrafast-") : -len(".dist-info")])
    return versions[0] if versions else None


def has_laya(venv):
    """Is the local decision engine installed in this environment?"""
    for packages in site_packages(venv):
        if any(packages.glob("laya-*.dist-info")) or (packages / "laya").is_dir():
            return True
    return False


def interpreter_runs(venv):
    """Can the environment's interpreter actually start?

    A previous install can leave this half-deleted: the virtualenv survives and the Python it
    pointed at does not (uninstalled, moved, or a broken symlink). Reading files cannot tell —
    only running it can, and `subprocess` raises rather than returning when it cannot.
    """
    python = venv_python(venv)
    if python is None:
        return False
    try:
        done = subprocess.run([str(python), "-c", "print(1)"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def has_entry_points(venv):
    """Are the commands the starters and Firefox call actually there?"""
    python = venv_python(venv)
    if python is None:
        return False
    base = python.parent
    for name in ("jev-firefox", "jev-register-host"):
        if not (base / name).exists() and not (base / f"{name}.exe").exists():
            return False
    return True


def laya_weights_cached():
    """The 644 MB of Laya weights, shared by every install on the machine."""
    cache = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    hub = cache / "hub"
    return any(hub.glob("models--convaiinnovations--laya*")) if hub.is_dir() else False


MANIFEST_NAME = "jev_ultrafast_host.json"


def manifest_path():
    """Where Firefox looks for the native-messaging host, per platform (no imports needed)."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Mozilla" / "NativeMessagingHosts" / MANIFEST_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts" / MANIFEST_NAME
    return Path.home() / ".mozilla" / "native-messaging-hosts" / MANIFEST_NAME


def registration(root, venv=None):
    """What Firefox was told, and whether it still points at this folder.

    Read from the manifest file, and confirmed with the installed package when there is an
    environment to ask: this script runs before anything is installed, so it cannot import the
    agent, and guessing "not registered" would make every start register again.
    """
    expected = Path(root).resolve()
    state = {"registered": False, "executable": None, "exists": False, "here": False, "manifest": str(manifest_path())}
    python = venv_python(Path(venv)) if venv else None
    if python is not None:
        probe = (
            "import json;from jev_ultrafast import native_setup as n;"
            "print(json.dumps(n.status(), default=str))"
        )
        try:
            done = subprocess.run(
                [str(python), "-c", probe], capture_output=True, text=True, check=False, cwd=str(root), timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is not None and done.returncode == 0:
            try:
                state.update(__import__("json").loads(done.stdout.strip().splitlines()[-1]))
            except (ValueError, IndexError):
                pass
    if not state.get("executable"):
        try:
            data = json.loads(manifest_path().read_text(encoding="utf-8"))
            state["registered"] = bool(data.get("name")) and bool(data.get("path"))
            state["executable"] = data.get("path")
        except (OSError, ValueError):
            pass
    executable = state.get("executable") or ""
    state["exists"] = bool(executable) and Path(executable).exists()
    state["here"] = bool(executable) and Path(executable).resolve().is_relative_to(expected)
    return state


def stale_paths(root):
    """Files a previous install left behind that a fresh one replaces rather than reuses."""
    root = Path(root)
    found = list(root.glob("*.egg-info"))
    for name in ("build", "dist"):
        candidate = root / name
        if candidate.is_dir() and not (candidate / "SMART_INSTALL_KEEP").exists():
            found.append(candidate)
    return found


def python_is_supported(version):
    if not version:
        return False
    parts = []
    for piece in str(version).split(".")[:2]:
        try:
            parts.append(int(piece))
        except ValueError:
            return False
    return tuple(parts) >= REQUIRED_PYTHON


def inspect(root):
    """Everything the plan is decided from, in one dictionary (also printed by --check)."""
    root = Path(root)
    venv = venv_dir(root)
    return {
        "root": str(root),
        "shipped": shipped_version(root),
        "venv": str(venv),
        "venv_exists": venv.exists(),
        "venv_python": str(venv_python(venv)) if venv_python(venv) else None,
        "venv_python_version": venv_python_version(venv),
        "installed": installed_version(venv) if venv.exists() else None,
        "interpreter_runs": interpreter_runs(venv) if venv.exists() else False,
        "entry_points": has_entry_points(venv) if venv.exists() else False,
        "laya": has_laya(venv) if venv.exists() else False,
        "laya_weights": laya_weights_cached(),
        "registration": registration(root, venv),
        "stale": [str(path) for path in stale_paths(root)],
    }


# ── deciding what to do ──────────────────────────────────────────────────────


def plan(root, reinstall=False, force_python=None):
    """The list of steps, with the reason for each. Pure: it reads and decides, never writes.

    Step kinds: `create`, `rebuild`, `reuse`, `prune`, `restore-laya`, `register`.
    """
    state = inspect(root)
    steps = []
    shipped = state["shipped"]
    version_ok = state["installed"] == shipped
    python_ok = python_is_supported(state["venv_python_version"])
    reusable = state["venv_exists"] and version_ok and python_ok and state["entry_points"]

    if state["venv_exists"] and not state["interpreter_runs"]:
        steps.append(_step("rebuild", "the existing environment is broken (its Python does not start): rebuilding it"))
    elif reinstall and state["venv_exists"]:
        steps.append(_step("rebuild", f"--reinstall was asked for: removing {state['venv']} and installing {shipped}"))
    elif not state["venv_exists"]:
        steps.append(_step("create", f"no private environment yet: installing {shipped}"))
    elif not reusable:
        if not state["interpreter_runs"]:
            reason = "its Python does not start"
        elif not python_ok:
            needed = f"{REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}"
            reason = (
                f"it was built by Python {state['venv_python_version'] or '?'},"
                f" and this agent needs {needed} or newer"
            )
        elif not version_ok:
            reason = f"it holds version {state['installed'] or '?'}, and this copy is {shipped}"
        else:
            reason = "its commands are missing, so it is not usable as it is"
        steps.append(_step("rebuild", f"replacing the existing environment ({reason})"))
    else:
        steps.append(
            _step(
                "reuse",
                f"already installed: {shipped} on Python {state['venv_python_version']} (nothing downloaded)",
            )
        )

    if state["stale"] and any("rebuild" in step["kind"] or "create" in step["kind"] for step in steps) is False:
        steps.append(
            _step(
                "prune",
                "removing build leftovers: " + ", ".join(Path(p).name for p in state["stale"]),
                paths=list(state["stale"]),
            )
        )
    if state["laya"] and any(step["kind"] == "rebuild" for step in steps):
        steps.append(
            _step(
                "restore-laya",
                "Laya was installed here: putting the package back"
                + (" (weights are already on disk, nothing large is downloaded)" if state["laya_weights"] else ""),
            )
        )
    registered = state["registration"].get("registered")
    if not registered:
        steps.append(_step("register", "registering the host with Firefox (so the sidebar starts it by itself)"))
    elif not state["registration"].get("here"):
        other = state["registration"].get("executable")
        steps.append(
            _step(
                "register",
                "Firefox was pointing at another copy of the agent"
                + (f" ({other})" if other else "")
                + " — registering this folder instead",
            )
        )
    return steps


def _step(kind, text, **extra):
    return {"kind": kind, "text": text, **extra}


# ── doing it ─────────────────────────────────────────────────────────────────


def _guard(paths, root):
    """Refuse to delete anything that is not the environment or a build leftover."""
    root = Path(root).resolve()
    for path in paths:
        resolved = Path(path).resolve()
        if resolved == root or root not in resolved.parents:
            raise SystemExit(f"refusing to remove {resolved}: it is outside the project")
        if resolved.name in NEVER_REMOVE:
            raise SystemExit(f"refusing to remove {resolved}: it is your data, not the installation")
        if resolved.name == ".venv":
            continue
        if not (resolved.name.endswith(".egg-info") or resolved.name in {"build", "dist"}):
            raise SystemExit(f"refusing to remove {resolved}: not part of an installation")


def remove(paths, root):
    """Delete the environment and build leftovers. Your keys, history and workspace stay."""
    paths = [Path(p) for p in paths]
    _guard(paths, root)
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)


def pip_commands(venv, root, with_laya=False):
    """The exact commands a fresh environment needs, in order."""
    python = venv_python(venv) or (Path(venv) / "bin" / "python")
    steps = [[str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"]]
    if with_laya and sys.platform in TORCH_PLATFORMS_USING_CPU_INDEX:
        # The CUDA build on PyPI is ~2.5 GB and Laya only needs the CPU one.
        steps.append([str(python), "-m", "pip", "install", "--quiet", "torch", "--index-url", CPU_TORCH_INDEX])
    if with_laya:
        steps.append([str(python), "-m", "pip", "install", "--quiet", LAYA_REQUIREMENT])
    # No --no-build-isolation: a fresh venv has no setuptools on Python 3.12+, and pip's
    # isolated build is the one thing that always works. It is cached after the first run.
    steps.append([str(python), "-m", "pip", "install", "--quiet", "-e", ".[documents]"])
    return steps


def apply(steps, root, python=None, run=subprocess.run, on_event=print):
    """Carry out a plan. Returns True when the agent is installed and registered afterwards."""
    root = Path(root)
    venv = venv_dir(root)
    rebuilt = False
    restored_laya = False
    for step in steps:
        on_event(step["text"])
        kind = step["kind"]
        if kind == "reuse":
            continue
        if kind in {"create", "rebuild"}:
            if kind == "rebuild":
                remove([venv], root)
            interpreter = python or sys.executable
            try:
                created = run([str(interpreter), "-m", "venv", str(venv)], check=False)
            except OSError as error:
                on_event(f"  [!] could not run {interpreter}: {error}")
                return False
            if getattr(created, "returncode", 0) != 0:
                on_event("  [!] could not create the private environment (is the venv module installed?)")
                return False
            rebuilt = True
            restored_laya = any(s["kind"] == "restore-laya" for s in steps)
            commands = pip_commands(venv, root, with_laya=restored_laya)
            for command in commands:
                try:
                    done = run(command, cwd=str(root), check=False)
                except OSError as error:
                    on_event(f"  [!] could not run the installer: {error}")
                    return False
                if getattr(done, "returncode", 0) != 0:
                    on_event("  [!] installation failed; check your internet connection and try again")
                    return False
            if restored_laya:
                _note_laya_restored(root)
        elif kind == "prune":
            remove([p for p in step.get("paths", [])] or _stale_from_text(root, step["text"]), root)
        elif kind == "restore-laya":
            continue  # already handled inside the rebuild
        elif kind == "register":
            entry = venv / "bin" / "jev-register-host"
            if not entry.exists():
                entry = venv / "Scripts" / "jev-register-host.exe"
            try:
                done = run([str(entry)], cwd=str(root), check=False, capture_output=True, text=True)
            except TypeError:  # a caller that injected a simpler run()
                done = run([str(entry)], cwd=str(root), check=False)
            except OSError:
                done = None
            message = getattr(done, "stdout", None)
            if message:
                on_event(message.rstrip())  # keep the order readable when the output is piped
            if done is None or getattr(done, "returncode", 0) != 0:
                on_event("  [!] could not register with Firefox: keep the starter window open instead")
    if rebuilt and restored_laya:
        on_event("Laya (the local engine) is back in the new environment.")
    if rebuilt and not restored_laya:
        # The agent's own background installer takes it from here, on the first start.
        on_event("Laya (the local engine) will be installed in the background on the first start.")
    return venv_python(venv) is not None


def _stale_from_text(root, text):
    """The leftovers named in a `prune` step, read back from the step itself."""
    names = text.split(": ", 1)[-1].split(", ")
    return [Path(root) / name for name in names if name]


def _note_laya_restored(root):
    """Remember that Laya is here, so the agent does not try to install it again."""
    marker = Path(root) / LOCAL_DATA[0]
    try:
        marker.write_text(
            json.dumps(
                {
                    "outcome": "installed",
                    "detail": "restored by the installer",
                    "when": int(__import__("time").time()),
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def describe(state):
    """The report `--check` prints: what is installed and what would happen."""
    if state["venv_exists"]:
        environment = [
            f"Environment:    {state['venv']}",
            f"  installed:    {state['installed'] or 'nothing usable'}",
            f"  built by:     Python {state['venv_python_version'] or '?'}",
            f"  interpreter:  {'starts' if state['interpreter_runs'] else 'does not start (broken)'}",
            f"  commands:     {'yes' if state['entry_points'] else 'no'}",
        ]
    else:
        environment = [
            f"Environment:    {state['venv']}",
            "  state:        not created yet (the next run creates it)",
        ]
    lines = [
        f"Project:        {state['root']}",
        f"Version:        this copy ships {state['shipped']}",
        *environment,
        f"Laya:           {'installed' if state['laya'] else 'not installed'}"
        f" (weights {'cached' if state['laya_weights'] else 'not downloaded'})",
        f"Firefox host:   {'registered' if state['registration'].get('registered') else 'not registered'}"
        f"{'' if state['registration'].get('here') else ' (or pointing at another folder)'}",
        f"Leftovers:      {', '.join(Path(p).name for p in state['stale']) or 'none'}",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install or update the agent, keeping your data.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--python", default=None, help="interpreter used to create the environment")
    parser.add_argument("--reinstall", action="store_true", help="remove the environment and install fresh")
    parser.add_argument("--check", action="store_true", help="report only; change nothing")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    parser.add_argument("--quiet", action="store_true", help="only print what is not 'nothing to do'")
    arguments = parser.parse_args(argv)

    state = inspect(arguments.root)
    steps = plan(arguments.root, reinstall=arguments.reinstall, force_python=arguments.python)

    if arguments.check:
        print(describe(state))
        print("\nWould do: " + ("; ".join(step["text"] for step in steps) or "nothing"))
        return 0
    if arguments.dry_run:
        print(describe(state))
        print()
        for step in steps:
            print(f"  [{step['kind']}] {step['text']}")
        return 0

    proactive = [step for step in steps if step["kind"] != "reuse"]
    if arguments.quiet and not proactive:
        print(f"Already installed and current: {state['shipped']} (nothing downloaded).")
        return 0
    visible = [step for step in steps if not (arguments.quiet and step["kind"] == "reuse")]
    ok = apply(visible, arguments.root, python=arguments.python)
    if ok:
        print("")
        print(f"Ready: version {state['shipped']} in {state['venv']}.")
        print("Your keys (.env), your run history (artifacts/) and your workspace files were not touched.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
