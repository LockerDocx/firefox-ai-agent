"""The shipped model is `z-ai/glm-5.3-flash` with thinking off, in all three roles.

This is an explicit product decision (2026-09-27), not a measurement: the same key measured
`z-ai/glm-5.3` at 1.9 s per executor step against 37.4 s for flash (docs/providers.md carries
both tables). The tests below are what makes the decision visible: if someone changes the
derivation, or leaves an older install pinned to the old model, they fail here.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jev_ultrafast import parameters, providers

ROOT = Path(__file__).resolve().parents[1]

OLD = "z-ai/glm-5.3"
NEW = "z-ai/glm-5.3-flash"


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith(("JEV_", "GROQ_", "NVIDIA_", "DEEPSEEK_", "OPENAI_", "POLICY_", "PLANNER_", "TEXT_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JEV_ENV_FILE", str(tmp_path / "env"))
    return monkeypatch


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(parameters, "CONFIG_PATH", tmp_path / "model-config.json")
    return tmp_path / "model-config.json"


# ── what ships ───────────────────────────────────────────────────────────────


def test_every_role_derives_flash(clean_env):
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    for role in ("planner", "policy", "text"):
        assert providers.selection_for(role) == ("nvidia", NEW), role


def test_every_role_sends_thinking_off(clean_env):
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    for role in ("planner", "policy", "text"):
        resolved = providers.resolve(role)
        assert resolved["model"] == NEW, role
        assert resolved["reasoning"] == "none", role
        assert resolved["params"] == {"reasoning": "none"}, role


def test_the_old_model_is_nowhere_in_the_shipped_table():
    """A single place to change and a single place to read: the derivation."""
    for role, model in providers.DERIVED_MODELS["nvidia"].items():
        assert model == NEW, role
    assert OLD not in set(providers.DERIVED_MODELS["nvidia"].values())


def test_the_help_text_names_the_model_that_runs(clean_env):
    """The message a user without a key reads must not promise another model."""
    assert NEW in providers.NO_KEY_HELP
    assert not re.search(rf"{re.escape(OLD)}(?!-flash)", providers.NO_KEY_HELP)


# ── an older install: the .env the starter finds ─────────────────────────────


def _sed_line():
    """The migration command as it is written in start-host.sh, not a copy of it."""
    script = (ROOT / "start-host.sh").read_text(encoding="utf-8")
    for line in script.splitlines():
        if line.strip().startswith("sed -i.bak -E") and "glm-5" in line:
            return line.strip().replace("-i.bak", "-i")
    pytest.skip("start-host.sh has no migration line (POSIX-only test)")


@pytest.mark.skipif(shutil.which("sed") is None, reason="sed is not installed")
def test_the_starter_moves_a_pinned_old_model(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# settings\n"
        "NVIDIA_API_KEY=nvapi-x\n"
        f"PLANNER_MODEL={OLD}\n"
        f"POLICY_MODEL=zai/glm-5.3\n"  # the wrong id old templates shipped
        "TEXT_MODEL=openai/gpt-oss-20b\n"  # a deliberate choice: untouched
        f"# PLANNER_MODEL={OLD}\n",  # a comment: untouched
        encoding="utf-8",
    )
    subprocess.run(["bash", "-c", _sed_line()], cwd=tmp_path, check=True)
    saved = env.read_text(encoding="utf-8")
    assert f"PLANNER_MODEL={NEW}" in saved, "the pinned old model must move"
    assert f"POLICY_MODEL={NEW}" in saved, "and so must the wrong id an old template shipped"
    assert "TEXT_MODEL=openai/gpt-oss-20b" in saved, "another model is the user's choice"
    assert f"# PLANNER_MODEL={OLD}" in saved, "comments are not configuration"
    assert saved.count(NEW) == 2


@pytest.mark.skipif(shutil.which("sed") is None, reason="sed is not installed")
def test_a_flash_line_is_left_alone(tmp_path):
    """The migration must not rewrite what it already did (or it would run every start)."""
    env = tmp_path / ".env"
    env.write_text(f"PLANNER_MODEL={NEW}\nPOLICY_MODEL={NEW}\n", encoding="utf-8")
    before = env.read_text(encoding="utf-8")
    subprocess.run(["bash", "-c", _sed_line()], cwd=tmp_path, check=True)
    assert env.read_text(encoding="utf-8") == before


def test_the_windows_starter_carries_the_same_rule():
    """The .bat cannot be run here, so its pattern is read: same two shapes, same replacement."""
    script = (ROOT / "start-host.bat").read_text(encoding="utf-8")
    assert "zai|z-ai" in script or "zai|z-ai" in script.replace(" ", "")
    assert NEW in script
    assert "JEV_KEEP_MODEL" in script, "and the same escape hatch"


# ── an older install: the model chosen in the panel ──────────────────────────


def test_a_saved_selection_on_the_old_default_is_moved(isolated_config, monkeypatch):
    parameters.save_config({"models": {"planner": {"provider": "nvidia", "model": OLD}}})
    moved = parameters.migrate_old_default_model()
    assert moved == ["planner"]
    assert parameters.load_config()["models"]["planner"]["model"] == NEW
    assert parameters.load_config()["models_migrated_from"] == OLD, "the file keeps the reason"


def test_a_deliberate_choice_is_left_untouched(isolated_config, monkeypatch):
    """Only the exact old default on NVIDIA. Anything else is somebody's decision."""
    parameters.save_config({
        "models": {
            "policy": {"provider": "groq", "model": "openai/gpt-oss-20b"},
            "text": {"provider": "nvidia", "model": "openai/gpt-oss-20b"},
            "planner": {"provider": "nvidia", "model": OLD},
        }
    })
    assert parameters.migrate_old_default_model() == ["planner"]
    config = parameters.load_config()
    assert config["models"]["policy"]["model"] == "openai/gpt-oss-20b"
    assert config["models"]["text"]["model"] == "openai/gpt-oss-20b"


def test_keeping_the_old_model_is_one_variable(isolated_config, monkeypatch):
    monkeypatch.setenv("JEV_KEEP_MODEL", "1")
    parameters.save_config({"models": {"planner": {"provider": "nvidia", "model": OLD}}})
    assert parameters.migrate_old_default_model() == []
    assert parameters.load_config()["models"]["planner"]["model"] == OLD


def test_the_migration_only_writes_once(isolated_config):
    parameters.save_config({"models": {"planner": {"provider": "nvidia", "model": OLD}}})
    assert parameters.migrate_old_default_model() == ["planner"]
    assert parameters.migrate_old_default_model() == [], "a second run has nothing to do"


def test_the_panel_and_the_derivation_end_up_saying_the_same_thing(clean_env, isolated_config):
    """After the migration, applying the saved config must leave flash in force."""
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    parameters.save_config({"models": {"planner": {"provider": "nvidia", "model": OLD}}})
    parameters.apply_saved_config()
    assert os.environ["PLANNER_MODEL"] == NEW
    assert providers.resolve("planner")["model"] == NEW
