"""The smart installer: replace a previous installation, keep the user's data, remember Laya.

The rules the user asked for, made executable:

* an installation that already exists and is old is *removed* and replaced, never patched on top;
* Laya already installed is never installed a second time;
* your keys (`.env`), your history (`artifacts/`) and your files (`workspace/`) are never deleted;
* `--check` and `--dry-run` change nothing at all.

`interpreter_runs()` executes the environment's interpreter, so the fake environments here get a
*real* symlink to `sys.executable` — that is what makes the difference between "installed" and
"installed but unusable" testable without building a virtualenv per test.
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
INSTALL = ROOT / "scripts" / "install.py"
POSIX = pytest.mark.skipif(os.name != "posix", reason="the fake environments are POSIX")


def load_installer():
    spec = importlib.util.spec_from_file_location("jev_install", INSTALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load_installer()


# ── a project copy, and fake environments inside it ─────────────────────────


@pytest.fixture
def project(tmp_path):
    """A copy of this project: pyproject.toml is the version it ships, and nothing else matters."""
    (tmp_path / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def make_venv(project, version=None, python_version="3.13.5", entry_points=True, laya=False, python="real"):
    """Build the parts of an environment the installer looks at.

    python="real" links to the interpreter running the tests, so the environment genuinely works;
    "missing" leaves no interpreter at all (someone removed Python), "broken" leaves a file that
    cannot be executed (a half-finished install).
    """
    venv = Path(project) / ".venv"
    binary = venv / ("Scripts" if os.name == "nt" else "bin")
    binary.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text(f"home = /usr\nversion = {python_version}\n", encoding="utf-8")
    line = ".".join(python_version.split(".")[:2])
    packages = venv / "Lib" / "site-packages" if os.name == "nt" else venv / "lib" / f"python{line}" / "site-packages"
    packages.mkdir(parents=True, exist_ok=True)
    if version:
        (packages / f"jev_ultrafast-{version}.dist-info").mkdir()
    if laya:
        (packages / "laya").mkdir()
    interpreter = binary / ("python.exe" if os.name == "nt" else "python")
    if python == "real":
        interpreter.symlink_to(sys.executable)
    elif python == "broken":
        interpreter.write_text("not an interpreter\n", encoding="utf-8")
        interpreter.chmod(interpreter.stat().st_mode & ~stat.S_IEXEC)
    if entry_points:
        for name in ("jev-firefox", "jev-register-host"):
            (binary / (f"{name}.exe" if os.name == "nt" else name)).write_text("#!/bin/sh\n", encoding="utf-8")
    return venv


@pytest.fixture(autouse=True)
def no_real_firefox(monkeypatch, tmp_path):
    """Never read (or write) the Firefox of the machine running the tests.

    `HOME` matters as well as the path helper: the installer asks the installed package for its
    opinion by running it, and that subprocess works out the manifest location from its own
    environment. Without this, a developer's real registration would decide the test's outcome.
    """
    monkeypatch.setattr(install, "manifest_path", lambda: tmp_path / "manifest.json")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))


@pytest.fixture(autouse=True)
def no_real_laya_weights(monkeypatch):
    """The weights cache of the machine must not change any test's outcome."""
    monkeypatch.setattr(install, "laya_weights_cached", lambda: False)


def kinds(steps):
    return [step["kind"] for step in steps]


def text_of(steps):
    return " ".join(step["text"] for step in steps)


# ── what it decides ─────────────────────────────────────────────────────────


def test_a_folder_without_an_environment_is_installed_from_scratch(project):
    steps = install.plan(project)
    assert kinds(steps) == ["create", "register"]
    assert install.shipped_version(project) in steps[0]["text"]


def test_an_offline_folder_writes_nothing(project):
    """`--check` and `--dry-run` are the reassurance: they decide, they never touch."""
    before = sorted(p.name for p in project.iterdir())
    assert install.main(["--check", "--root", str(project)]) == 0
    assert install.main(["--dry-run", "--root", str(project)]) == 0
    assert sorted(p.name for p in project.iterdir()) == before
    assert not (project / ".venv").exists()


def test_an_environment_at_the_current_version_is_left_alone(project):
    shipped = install.shipped_version(project)
    make_venv(project, version=shipped)
    assert kinds(install.plan(project)) == ["reuse", "register"]
    assert "nothing downloaded" in text_of(install.plan(project))


def test_a_previous_version_is_removed_and_replaced(project):
    """The case the user described: the whole program is installed, an older one."""
    make_venv(project, version="0.9.9")
    steps = install.plan(project)
    assert kinds(steps)[0] == "rebuild"
    assert "0.9.9" in steps[0]["text"] and install.shipped_version(project) in steps[0]["text"]


def test_an_environment_built_by_an_old_python_is_replaced(project):
    make_venv(project, version=install.shipped_version(project), python_version="3.10.12")
    steps = install.plan(project)
    assert kinds(steps)[0] == "rebuild"
    assert "3.10.12" in steps[0]["text"] and "3.11" in steps[0]["text"]


@POSIX
def test_an_environment_whose_python_does_not_start_is_replaced(project):
    """Half-deleted installs happen: the interpreter is gone and the folder is still there."""
    make_venv(project, version=install.shipped_version(project), python="missing")
    steps = install.plan(project)
    assert kinds(steps)[0] == "rebuild"
    assert "broken" in steps[0]["text"]


@POSIX
def test_an_environment_that_cannot_be_executed_is_replaced(project):
    make_venv(project, version=install.shipped_version(project), python="broken")
    assert kinds(install.plan(project))[0] == "rebuild"


def test_an_environment_without_the_commands_is_replaced(project):
    make_venv(project, version=install.shipped_version(project), entry_points=False)
    steps = install.plan(project)
    assert kinds(steps)[0] == "rebuild" and "commands" in steps[0]["text"]


def test_reinstall_rebuilds_even_when_everything_looks_current(project):
    make_venv(project, version=install.shipped_version(project))
    steps = install.plan(project, reinstall=True)
    assert kinds(steps)[0] == "rebuild" and "--reinstall" in steps[0]["text"]


def test_build_leftovers_are_removed_when_the_environment_is_kept(project):
    make_venv(project, version=install.shipped_version(project))
    (project / "build").mkdir()
    (project / "jev_ultrafast.egg-info").mkdir()
    steps = install.plan(project)
    assert "prune" in kinds(steps)
    prune = [step for step in steps if step["kind"] == "prune"][0]
    assert {Path(path).name for path in prune["paths"]} == {"build", "jev_ultrafast.egg-info"}


def test_build_leftovers_need_no_extra_step_when_the_environment_goes(project):
    """A rebuild takes the environment; removing the leftovers as well would be noise."""
    make_venv(project, version="0.1.0")
    (project / "dist").mkdir()
    assert "prune" not in kinds(install.plan(project))


# ── Laya is never installed twice ───────────────────────────────────────────


def test_laya_already_there_is_put_back_after_a_rebuild(project):
    make_venv(project, version="0.9.9", laya=True)
    steps = install.plan(project)
    assert "restore-laya" in kinds(steps)


def test_laya_already_there_is_untouched_when_nothing_is_rebuilt(project):
    make_venv(project, version=install.shipped_version(project), laya=True)
    assert kinds(install.plan(project)) == ["reuse", "register"]


def test_cached_weights_are_named_so_nobody_expects_a_download(project, monkeypatch):
    make_venv(project, version="0.9.9", laya=True)
    monkeypatch.setattr(install, "laya_weights_cached", lambda: True)
    steps = install.plan(project)
    assert "weights are already on disk" in text_of(steps)


@POSIX
def test_restoring_laya_installs_the_package_and_says_so(project):
    make_venv(project, version="0.9.9", laya=True)
    (project / ".env").write_text("NVIDIA_API_KEY=prueba\n", encoding="utf-8")
    (project / "workspace").mkdir()
    (project / "workspace" / "nota.txt").write_text("mío\n", encoding="utf-8")
    seen = []
    commands = []

    def fake_run(command, **kwargs):
        commands.append([str(piece) for piece in command])
        if command[-2:] == ["-m", "venv"] or (len(command) > 2 and command[1:3] == ["-m", "venv"]):
            make_venv(project, version=install.shipped_version(project))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert install.apply(install.plan(project), project, run=fake_run, on_event=seen.append) is True
    flattened = " ".join(" ".join(command) for command in commands)
    assert install.LAYA_REQUIREMENT in flattened, "Laya comes back with the environment"
    assert "-e .[documents]" in flattened or "-e .[documents]" in flattened.replace("  ", " ")
    assert "Laya (the local engine) is back" in " ".join(seen)
    assert (project / ".env").read_text(encoding="utf-8") == "NVIDIA_API_KEY=prueba\n"
    assert (project / "workspace" / "nota.txt").read_text(encoding="utf-8") == "mío\n"
    marker = json.loads((project / "laya-install.json").read_text(encoding="utf-8"))
    assert marker["detail"] == "restored by the installer", "the agent reads this to skip Laya"


def test_a_fresh_install_leaves_laya_to_the_agent(project):
    seen = []

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    install.apply(install.plan(project), project, run=fake_run, on_event=seen.append)
    assert "will be installed in the background on the first start" in " ".join(seen)
    assert not (project / "laya-install.json").exists()


def test_laya_comes_from_the_cpu_index_on_the_systems_that_need_it(project, monkeypatch):
    """The CUDA build on PyPI is ~2.5 GB; a CPU-only Laya downloads a fraction of that."""
    for platform_name, expected in (("linux", True), ("win32", True), ("darwin", False)):
        monkeypatch.setattr(install.sys, "platform", platform_name)
        commands = install.pip_commands(project / ".venv", project, with_laya=True)
        flat = " ".join(" ".join(command) for command in commands)
        assert (install.CPU_TORCH_INDEX in flat) is expected, platform_name


def test_nothing_is_installed_on_top_of_an_existing_environment(project):
    """The installer must not add build flags that break a fresh environment without setuptools."""
    flat = " ".join(" ".join(command) for command in install.pip_commands(project / ".venv", project))
    assert "--no-build-isolation" not in flat


# ── what it must never delete ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.bak",
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
    ],
)
def test_your_data_is_refused_even_when_asked_for(project, name):
    with pytest.raises(SystemExit):
        install.remove([project / name], project)


def test_nothing_outside_the_project_can_be_removed(project, tmp_path):
    outside = tmp_path.parent / "ajeno"
    outside.mkdir(exist_ok=True)
    with pytest.raises(SystemExit):
        install.remove([outside], project)


def test_the_environment_and_leftovers_are_the_only_things_removed(project):
    venv = make_venv(project, version="0.9.9")
    (project / "build").mkdir()
    install.remove([venv, project / "build"], project)
    assert not venv.exists() and not (project / "build").exists()


def test_a_rebuild_keeps_the_keys_and_the_history_on_disk(project):
    """The whole point: the installation goes, the user stays."""
    make_venv(project, version="0.9.9")
    (project / ".env").write_text("NVIDIA_API_KEY=prueba\n", encoding="utf-8")
    (project / "artifacts").mkdir()
    (project / "artifacts" / "runs.jsonl").write_text('{"run": 1}\n', encoding="utf-8")
    (project / "workspace").mkdir()
    (project / "workspace" / "nota.txt").write_text("mi fichero\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        if "-m" in command and "venv" in command:
            make_venv(project, version=install.shipped_version(project))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert install.apply(install.plan(project), project, run=fake_run, on_event=lambda _: None) is True
    assert (project / ".env").read_text(encoding="utf-8") == "NVIDIA_API_KEY=prueba\n"
    assert (project / "artifacts" / "runs.jsonl").read_text(encoding="utf-8") == '{"run": 1}\n'
    assert (project / "workspace" / "nota.txt").read_text(encoding="utf-8") == "mi fichero\n"


# ── Firefox is told, and told once ──────────────────────────────────────────


def test_the_host_is_registered_when_firefox_does_not_know_about_it(project):
    make_venv(project, version=install.shipped_version(project))
    assert kinds(install.plan(project)) == ["reuse", "register"]


def test_a_registered_host_is_not_registered_again(project):
    """Running the starter twice must not rewrite anything the second time."""
    make_venv(project, version=install.shipped_version(project))
    install.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    install.manifest_path().write_text(
        json.dumps({"name": "jev_ultrafast_host", "path": str(project / ".venv" / "bin" / "jev-firefox-native")}),
        encoding="utf-8",
    )
    assert kinds(install.plan(project)) == ["reuse"]


def test_a_host_pointing_at_another_copy_is_taken_over_and_said_out_loud(project, tmp_path):
    make_venv(project, version=install.shipped_version(project))
    other = project.parent / "otra-copia"  # a second copy of the agent, outside this folder
    (other / ".venv" / "bin").mkdir(parents=True)
    install.manifest_path().parent.mkdir(parents=True, exist_ok=True)
    install.manifest_path().write_text(
        json.dumps({"name": "jev_ultrafast_host", "path": str(other / ".venv" / "bin" / "jev-firefox-native")}),
        encoding="utf-8",
    )
    steps = install.plan(project)
    assert kinds(steps) == ["reuse", "register"]
    assert "another copy" in steps[1]["text"] and str(other) in steps[1]["text"]


@POSIX
def test_the_installed_package_is_asked_before_the_manifest_file(project, monkeypatch):
    """The agent knows things the file does not; ask it when there is something to run."""
    make_venv(project, version=install.shipped_version(project))
    answer = json.dumps({"registered": True, "executable": str(project / ".venv" / "bin" / "jev-firefox-native")})
    def answered(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=answer + "\n")

    monkeypatch.setattr(install, "subprocess", SimpleNamespace(run=answered))
    state = install.registration(project, project / ".venv")
    assert state["registered"] is True and state["here"] is True


# ── the report ──────────────────────────────────────────────────────────────


def test_check_says_what_would_happen_without_doing_it(project):
    make_venv(project, version="0.9.9")
    report = install.describe(install.inspect(project))
    assert "0.9.9" in report and install.shipped_version(project) in report
    assert "interpreter" in report


def test_the_report_is_honest_when_there_is_no_environment_yet(project):
    report = install.describe(install.inspect(project))
    assert "not created yet" in report
    assert "does not start" not in report, "no environment is not the same as a broken one"


# ── the starters hand the whole job to the installer ────────────────────────


@pytest.mark.parametrize("script", ["start-host.sh", "start-host.command"])
def test_the_shell_starters_call_the_installer_with_the_interpreter_they_found(script):
    text = (ROOT / script).read_text(encoding="utf-8")
    assert '"$PY" scripts/install.py' in text, script
    assert "python3 -m venv" not in text and "pip install" not in text, "one place installs, not two"


def test_the_windows_starter_calls_the_installer_with_the_interpreter_it_found():
    text = (ROOT / "start-host.bat").read_text(encoding="utf-8")
    assert "%PY% scripts\\install.py" in text
    assert "pip install" not in text and "-m venv .venv" not in text, "one place installs, not two"


def test_no_starter_registers_firefox_by_itself_any_more():
    """Registration lives in the installer, so it happens once and reports honestly."""
    for script in ("start-host.sh", "start-host.command", "start-host.bat"):
        text = (ROOT / script).read_text(encoding="utf-8")
        assert "jev-register-host" not in text, script


def test_the_installer_never_assumes_which_python_is_installed():
    text = INSTALL.read_text(encoding="utf-8")
    assert "sys.executable" in text
    assert '"python3"' not in text and "'python3'" not in text, "the caller's interpreter decides"


def test_a_failed_install_is_reported_instead_of_crashing(project, capsys):
    class Boom(Exception):
        pass

    def exploding_run(command, **kwargs):
        if "venv" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="no internet")

    assert install.apply(install.plan(project), project, run=exploding_run, on_event=print) is False
    assert "installation failed" in capsys.readouterr().out


@POSIX
def test_the_installer_runs_end_to_end_on_this_copy():
    """The real thing, no fakes: this must answer in under a second and never fail."""
    done = subprocess.run(
        [sys.executable, str(INSTALL), "--check", "--root", str(ROOT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert f"this copy ships {install.shipped_version(ROOT)}" in done.stdout
    assert "Would do:" in done.stdout
