"""The starters must cope with every system we claim to support.

openSUSE Leap ships Python 3.11 as python3 and 3.12 as python312, Fedora calls it
python3.12, Debian splits the venv module out, Arch is always current, Windows has
both the `py` launcher and python.exe. These tests run the real scripts against
fake interpreters, so the message a user gets is the message we ship.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
POSIX = pytest.mark.skipif(os.name != "posix", reason="the shell starters are POSIX")

OLD = "#!/bin/sh\ncase \"$*\" in *version_info*) exit 1;; *--version*) echo \"Python 3.6.15\";; esac\nexit 0\n"
NEW = "#!/bin/sh\ncase \"$*\" in *version_info*) exit 0;; *--version*) echo \"Python 3.12.9\";; esac\nexit 0\n"

OS_RELEASES = {
    "suse": 'ID="opensuse-leap"\nID_LIKE="suse"\n',
    "tumbleweed": 'ID="opensuse-tumbleweed"\nID_LIKE="suse"\n',
    "debian": "ID=debian\n",
    "ubuntu": 'ID=ubuntu\nID_LIKE=debian\n',
    "fedora": "ID=fedora\n",
    "arch": "ID=arch\n",
}


def stub(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def sandbox(tmp_path):
    """Fake PATHs: too-old interpreters, one modern python3.12, a macOS uname."""

    def build(kinds):
        directory = tmp_path / f"bin-{'-'.join(sorted(kinds))}"
        for name in ("python3.13", "python3.12", "python3.11", "python3", "python"):
            stub(directory, name, OLD)
        if "modern" in kinds:
            stub(directory, "python3.12", NEW)  # what `zypper install python312` gives you
        if "darwin" in kinds:
            stub(directory, "uname", "#!/bin/sh\necho Darwin\n")
        return directory

    return build


def run_starter(script, sandbox, os_release=None, dry_run=True, extra_env=None):
    release = Path(sandbox) / f"os-release-{os_release or 'none'}"
    if os_release:
        release.write_text(OS_RELEASES[os_release], encoding="utf-8")
    environment = {
        "PATH": f"{sandbox}:/usr/bin:/bin",
        "HOME": str(Path(sandbox) / "home"),
        "JEV_OS_RELEASE": str(release if os_release else Path(sandbox) / "missing"),
    }
    if dry_run:
        environment["JEV_START_DRY_RUN"] = "1"
    environment.update(extra_env or {})
    return subprocess.run(
        ["bash", str(ROOT / script)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ── finding an interpreter ──────────────────────────────────────────────────


@POSIX
def test_a_modern_interpreter_is_found_however_it_is_named(sandbox):
    result = run_starter("start-host.sh", sandbox("modern"), "suse")
    assert result.returncode == 0
    assert result.stdout.strip() == "PYTHON=python3.12"  # python312 on openSUSE


@POSIX
def test_an_old_interpreter_is_rejected_with_the_exact_command_for_that_distro(sandbox):
    expectations = {
        "suse": "sudo zypper install python312 python312-pip",
        "tumbleweed": "sudo zypper install python312 python312-pip",
        "debian": "sudo apt update && sudo apt install python3 python3-pip python3-venv",
        "ubuntu": "sudo apt update && sudo apt install python3 python3-pip python3-venv",
        "fedora": "sudo dnf install python3.12 python3-pip",
        "arch": "sudo pacman -S python",
    }
    for distro, command in expectations.items():
        result = run_starter("start-host.sh", sandbox("old"), distro)
        assert result.returncode == 1, distro
        assert command in result.stdout, f"{distro}: {result.stdout}"
        assert "Python 3.6.15 - too old" in result.stdout, "the message must say what it found"


@POSIX
def test_macos_gets_its_own_hint(sandbox):
    result = run_starter("start-host.command", sandbox("old-darwin"), "suse", dry_run=False)
    assert result.returncode == 1
    assert "brew install python@3.12" in result.stdout
    assert "zypper" not in result.stdout


@POSIX
def test_an_unknown_system_still_gets_an_answer(sandbox):
    result = run_starter("start-host.sh", sandbox("old"), None)
    assert result.returncode == 1
    assert "https://www.python.org/downloads/" in result.stdout


@POSIX
def test_the_requirement_is_311_not_312():
    """openSUSE Leap 15.6 ships 3.11: demanding 3.12 would lock those users out."""
    for script in ("start-host.sh", "start-host.command"):
        text = (ROOT / script).read_text(encoding="utf-8")
        assert "REQUIRED_MINOR=11" in text, script
        assert "sys.version_info >= ($REQUIRED_MAJOR, $REQUIRED_MINOR)" in text, script
        assert "Python $REQUIRED_MAJOR.$REQUIRED_MINOR or newer" in text, script
    windows = (ROOT / "start-host.bat").read_text(encoding="utf-8")
    assert "sys.version_info >= (3, 11)" in windows
    assert "Python 3.11 or newer" in windows
    assert 'requires-python = ">=3.11"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


# ── what the scripts do once they have an interpreter ───────────────────────


@POSIX
def test_the_starters_use_the_interpreter_they_found():
    """The chosen interpreter must run the install; assuming python3 breaks SUSE.

    Since the installer took over the environment, the starters do not create it themselves
    any more - but the interpreter they found is still the one that ends up inside `.venv`,
    because `scripts/install.py` builds it with `sys.executable`, never with a fixed name.
    """
    for script in ("start-host.sh", "start-host.command"):
        text = (ROOT / script).read_text(encoding="utf-8")
        assert '"$PY" scripts/install.py' in text, script
        assert "python3 -m venv" not in text, script
    installer = (ROOT / "scripts" / "install.py").read_text(encoding="utf-8")
    assert "sys.executable" in installer and "python3 -m venv" not in installer


def test_windows_discovers_the_launcher_and_the_store_stub_case():
    text = (ROOT / "start-host.bat").read_text(encoding="utf-8")
    assert 'py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"' in text
    assert 'set "PY=python"' in text, "a python.exe install must also be accepted"
    assert "%PY% scripts\\install.py" in text, "the installer runs under the interpreter that was found"
    assert "Microsoft Store" in text, "the Store stub is the most common Windows trap"


def test_the_dry_run_hook_the_tests_use_cannot_change_normal_behaviour():
    """JEV_START_DRY_RUN only short-circuits after a real interpreter was found."""
    text = (ROOT / "start-host.sh").read_text(encoding="utf-8")
    dry = text.index("JEV_START_DRY_RUN")
    assert dry > text.index("find_python"), "the hook must come after discovery"
    assert "exec .venv/bin/jev-firefox" in text[dry:], "and before the real work"

def test_the_package_metadata_survives_a_real_install():
    """A key that ends up in the wrong TOML table installs locally and fails in CI.

    `pip install -e .` is what caught it the first time: hatchling refused a
    `dependencies` entry living under `[project.urls]`, so every CI job died at
    the install step while the local suite stayed green (nothing re-installs).
    """
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["dependencies"], "the runtime dependencies must stay declared"
    urls = project.get("urls", {})
    assert urls, "the public URLs are part of the package metadata"
    unknown = set(urls) - {"Homepage", "Repository", "Issues", "Releases", "Documentation", "Changelog"}
    assert not unknown, f"not a project URL key: {unknown}"
    assert "dependencies" not in urls, "a misplaced key here breaks every install"

def test_the_double_click_starters_are_executable_in_the_repository():
    """A starter that arrives without its executable bit is not double-clickable.

    CI checks this too, but only after the commit has been pushed and a sandbox
    that rewrites the working tree with 0644 has already staged it. Checking the
    index here means the mistake is caught before it leaves the machine — and
    skipped, not failed, for anyone working from a ZIP without git.
    """
    import subprocess

    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    starters = ["start-host.sh", "start-host.command", "install-laya.sh", "install-laya.command"]
    salida = subprocess.run(["git", "ls-files", "-s", *starters],
                            cwd=ROOT, capture_output=True, text=True, check=False).stdout
    if not salida.strip():
        pytest.skip("git is not available here")
    perdidos = [linea.split("\t")[1] for linea in salida.strip().splitlines() if not linea.startswith("100755")]
    assert not perdidos, (
        f"these starters lost their executable bit: {perdidos}. "
        f"Fix with: git update-index --chmod=+x {' '.join(perdidos)}"
    )
