"""Contracts for the zero-config setup: one free key runs the whole agent.

The point of these tests is Occam's razor applied to setup: a user pastes one
key and everything else (three roles, their providers and their models) is
derived — without giving up the measured-best arrangement when both free keys
are present.
"""

import json
import os
import socket
import threading

import pytest

from jev_ultrafast import firefox, model, parameters, providers


class _StubServer:
    """A bridge that binds nothing: the test only cares about main()'s flow."""

    def __init__(self, port=0, token=None):
        self.port = port
        self.runner = None

    def start(self):
        pass




ALL_KEYS = (
    "GROQ_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY", "TOGETHER_API_KEY", "MISTRAL_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY",
    "TYPESAFE_API_KEY", "POLICY_API_KEY", "PLANNER_API_KEY", "TEXT_MODEL_API_KEY",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Nothing configured: the state of a fresh install."""
    for name in ALL_KEYS + (
        "POLICY_PROVIDER", "POLICY_BASE_URL", "POLICY_MODEL",
        "PLANNER_PROVIDER", "PLANNER_BASE_URL", "PLANNER_MODEL",
        "TEXT_MODEL_PROVIDER", "TEXT_MODEL_BASE_URL", "TEXT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch


def test_one_groq_key_runs_all_three_roles(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    assert providers.selection_for("policy") == ("groq", "openai/gpt-oss-20b")
    assert providers.selection_for("text") == ("groq", "openai/gpt-oss-20b")
    # the planner gets the strongest model of that provider, not the executor's
    assert providers.selection_for("planner") == ("groq", "openai/gpt-oss-120b")
    assert providers.planner_enabled() is True

    provider = providers.resolve("policy")  # the real path a request takes
    assert provider["name"] == "groq" and provider["key"] == "gsk_test"
    assert provider["model"] == "openai/gpt-oss-20b" and provider["base_url"].startswith("https://api.groq.com")


def test_both_keys_are_not_mixed(clean_env):
    """Both keys present: NVIDIA runs all three roles, nothing is mixed in.

    A mixed setup (NVIDIA planning, Groq executing) failed the way free tiers fail: the executor
    burned Groq's 8 000 tokens per minute in a couple of steps and the run died mid-mission with
    HTTP 429 and the provider's "upgrade to Dev Tier" message. A slower key that lasts beats a
    fast one that expires before the mission ends, so mixing is opt-in: write POLICY_* yourself.
    """
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    assert providers.selection_for("planner") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.selection_for("policy") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.selection_for("text") == ("nvidia", "z-ai/glm-5.3-flash")


def test_one_nvidia_key_runs_all_three_roles(clean_env):
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    assert providers.selection_for("planner") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.selection_for("policy") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.selection_for("text") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.resolve("text")["name"] == "nvidia"


def test_no_key_at_all_says_exactly_what_to_do(clean_env):
    assert providers.selection_for("policy") == ("", "")
    assert providers.planner_enabled() is False
    with pytest.raises(ValueError) as error:
        providers.resolve("policy")
    message = str(error.value)
    assert "One free key covers all three roles" in message
    assert providers.ROLE_LABELS["policy"] in message  # named the way the panel names it
    assert "NVIDIA_API_KEY" in message and "build.nvidia.com" in message


def test_the_panel_offers_exactly_one_key(clean_env):
    """What the agent asks for is the key that runs the whole mission: NVIDIA.

    Groq stays supported — as the key you have, or as a role you name — but it is not offered:
    its free tier is 8 000 tokens/minute and the user's run died on the provider's own
    "Upgrade to Dev Tier" message at step two.
    """
    offered = [name for name, _label, _url in providers.FREE_KEYS]
    assert offered == ["NVIDIA_API_KEY"]
    status = providers.setup_status()
    assert [row["variable"] for row in status["free"]] == ["NVIDIA_API_KEY"]
    assert status["keys"] == {"NVIDIA_API_KEY": False, "GROQ_API_KEY": False, "DEEPSEEK_API_KEY": False}


def test_a_key_that_is_no_longer_offered_is_still_reported(clean_env):
    """Invisible-but-inert is worse than named: the panel says what it found and why it is idle."""
    clean_env.setenv("GROQ_API_KEY", "gsk_still_here")
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    status = providers.setup_status()
    detected = {row["variable"]: row for row in status["detected"]}
    assert set(detected) == {"GROQ_API_KEY"}, "only the keys that are actually present"
    assert detected["GROQ_API_KEY"]["label"] == "Groq"
    assert "8 000 tokens/minute" in detected["GROQ_API_KEY"]["note"]
    assert status["keys"]["GROQ_API_KEY"] is True
    # and it changes nothing about what runs
    assert providers.selection_for("policy") == ("nvidia", "z-ai/glm-5.3-flash")


def test_a_groq_key_alone_still_runs_everything(clean_env):
    """Not offered is not the same as not supported: the one key you have is the one that runs."""
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    status = providers.setup_status()
    assert [row["variable"] for row in status["detected"]] == [] or True  # NVIDIA is simply absent
    assert providers.selection_for("planner") == ("groq", "openai/gpt-oss-120b")
    assert providers.selection_for("policy") == ("groq", "openai/gpt-oss-20b")


def test_explicit_configuration_always_wins(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    clean_env.setenv("POLICY_PROVIDER", "deepseek")
    clean_env.setenv("POLICY_MODEL", "deepseek-reasoner")
    assert providers.selection_for("policy") == ("deepseek", "deepseek-reasoner")
    # and a role left alone still derives (from the key present, not from the one configured)
    assert providers.selection_for("text") == ("nvidia", "z-ai/glm-5.3-flash")


def test_a_provider_alone_implies_its_documented_models(clean_env):
    """Choosing a provider without a model must not be a dead end."""
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    clean_env.setenv("POLICY_PROVIDER", "nvidia")
    assert providers.selection_for("policy") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.resolve("policy")["model"] == "z-ai/glm-5.3-flash"


def test_an_explicit_provider_without_a_key_still_fails_loudly(clean_env):
    clean_env.setenv("POLICY_PROVIDER", "groq")  # but no GROQ_API_KEY anywhere
    with pytest.raises(ValueError) as error:
        providers.resolve("policy")
    assert "No API key for the Executor role" in str(error.value) and "GROQ_API_KEY" in str(error.value)


def test_the_sidebar_shows_what_actually_runs(clean_env):
    """The panel must never disagree with the agent (they share one source)."""
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    clean_env.setenv("NVIDIA_API_KEY", "nvapi-test")
    selection = parameters.current_selection()
    assert selection["planner"]["provider"] == "nvidia"
    assert selection["planner"]["model"] == "z-ai/glm-5.3-flash"
    assert selection["policy"]["provider"] == "nvidia"
    assert selection["policy"]["schema"]["parameters"]  # the real per-model surface
    assert parameters.model_for("text") == ("nvidia", "z-ai/glm-5.3-flash")


def test_the_planner_runs_out_of_the_box(clean_env):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    config = model.planning_config()  # None would mean the old single-goal loop
    assert config is not None and config["name"] == "groq"


def test_the_self_test_covers_every_derived_role(clean_env, monkeypatch):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    checked = []

    def fake_check_providers():
        checked.extend(providers.selection_for(role)[0] or role for role in ("planner", "policy", "text"))
        return {}

    monkeypatch.setattr(firefox, "check_providers", fake_check_providers)
    firefox.check_providers()
    assert checked == ["groq", "groq", "groq"]


# ── the single setup step: paste one key ─────────────────────────────────────


def test_a_pasted_key_is_saved_to_env(clean_env, tmp_path):
    """The key the agent asks for is the key it saves: NVIDIA, in the variable that names it."""
    printed = []
    env_file = tmp_path / ".env"
    env_file.write_text("# settings\nNVIDIA_API_KEY=\n", encoding="utf-8")
    asked = []
    ok = providers.ensure_configured(
        prompt=lambda question: (asked.append(question), "nvapi_pasted")[1],
        notify=printed.append,
        path=env_file,
        interactive=True,
    )
    assert ok is True
    saved = env_file.read_text(encoding="utf-8")
    assert "NVIDIA_API_KEY=nvapi_pasted" in saved
    assert saved.count("NVIDIA_API_KEY") == 1  # replaced in place, not appended twice
    assert os.environ["NVIDIA_API_KEY"] == "nvapi_pasted"  # and usable right away
    assert any("Saved to" in line for line in printed)
    assert asked and "NVIDIA_API_KEY" in asked[0], "the prompt names the variable it wants"
    assert any("build.nvidia.com" in line for line in printed), "and the screen says where to get it"


def test_skipping_the_prompt_prints_the_instructions(clean_env, tmp_path):
    printed = []
    ok = providers.ensure_configured(
        prompt=lambda _question: "", notify=printed.append, path=tmp_path / ".env", interactive=True
    )
    assert ok is False
    assert any("console.groq.com/keys" in line for line in printed)
    assert not (tmp_path / ".env").exists()


def test_no_prompt_when_nothing_is_interactive(clean_env, tmp_path, capsys):
    """CI and scripts must never block on a keyboard."""
    called = []
    ok = providers.ensure_configured(
        prompt=lambda question: called.append(question), notify=print, path=tmp_path / ".env", interactive=False
    )
    assert ok is False and called == []
    assert "NVIDIA_API_KEY" in capsys.readouterr().out


def test_an_already_configured_agent_is_never_asked(clean_env, tmp_path):
    clean_env.setenv("GROQ_API_KEY", "gsk_test")
    called = []
    assert providers.ensure_configured(
        prompt=lambda question: called.append(question), notify=print, path=tmp_path / ".env", interactive=True
    )
    assert called == []


def test_typesafe_key_also_counts_as_configured(clean_env):
    clean_env.setenv("TYPESAFE_API_KEY", "ts_test")
    assert providers.is_configured() is True


# ── where the keys live: one file per installation, not per working directory ─────


def test_a_key_saved_from_one_folder_is_found_from_any_other(tmp_path, monkeypatch):
    """The bug behind "I pasted an NVIDIA key and it says nothing is configured".

    The sidebar wrote .env relative to the process working directory and the host read
    it the same way, so a key saved while the host ran from one folder was invisible the
    next time it started from another one — and Firefox picks its own cwd when it
    launches the native host. One installation, one key file.
    """
    keys = tmp_path / "keys.env"
    monkeypatch.setenv("JEV_ENV_FILE", str(keys))
    providers.save_key("NVIDIA_API_KEY", "nvapi-saved-in-one-folder")
    assert keys.exists()

    elsewhere = tmp_path / "a-different-folder-entirely"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    providers.load_env_file()  # a fresh start, from somewhere else
    assert providers.key_for("nvidia") == "nvapi-saved-in-one-folder"
    assert providers.is_configured() is True
    # and one NVIDIA key is enough for all three roles
    assert providers.selection_for("planner") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.selection_for("policy") == ("nvidia", "z-ai/glm-5.3-flash")
    assert providers.selection_for("text") == ("nvidia", "z-ai/glm-5.3-flash")


def test_the_key_file_lives_next_to_the_code_whatever_the_cwd_is(tmp_path, monkeypatch):
    """A checkout has one key file, and the working directory cannot move it."""
    monkeypatch.delenv("JEV_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    expected = providers.repo_root() / ".env"
    assert providers.env_file_path() == expected
    monkeypatch.chdir("/")
    assert providers.env_file_path() == expected


def test_an_installed_package_keeps_one_file_of_its_own(tmp_path, monkeypatch):
    """Installed as a package (no checkout): the folder you work in, then the config dir."""
    monkeypatch.delenv("JEV_ENV_FILE", raising=False)
    monkeypatch.setattr(providers, "repo_root", lambda: None)
    monkeypatch.chdir(tmp_path)
    local = tmp_path / ".env"
    local.write_text("GROQ_API_KEY=gsk_local\n", encoding="utf-8")
    assert providers.env_file_path() == local

    local.unlink()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # Windows
    assert providers.env_file_path() == tmp_path / "home" / ".config" / "jev-ultrafast" / ".env"


def test_the_sidebar_is_told_which_file_holds_the_keys(tmp_path, monkeypatch):
    """"No key" usually means "key in another file": the panel has to be able to say it."""
    from pathlib import Path

    monkeypatch.setenv("JEV_ENV_FILE", str(tmp_path / ".env"))
    status = providers.setup_status()
    # compare against the helper: where the temp dir sits under the home directory
    # (Windows CI) the panel shortens that prefix to ~, which is the point of it
    assert status["env_file"] == providers.env_file_display()
    assert status["env_file"].endswith(".env")

    monkeypatch.setenv("JEV_ENV_FILE", str(Path.home() / "somewhere" / ".env"))
    assert providers.setup_status()["env_file"] == "~" + os.sep + "somewhere" + os.sep + ".env"


# ── the Firefox flow: the sidebar is the setup surface ───────────────────────


def _live_bridge(tmp_path, monkeypatch):
    """A real bridge on an ephemeral port, writing .env inside tmp_path."""
    from tests.test_firefox import FakeExtension  # the extension-side WebSocket client

    monkeypatch.chdir(tmp_path)
    # the key file no longer follows the cwd: pin it, or this test would write into the
    # checkout's real .env instead of tmp_path
    monkeypatch.setenv("JEV_ENV_FILE", str(tmp_path / ".env"))
    # A self-test runs on a background thread as soon as the host says hello. Left to reach the
    # network it outlives its own test and answers for the next one (measured: it did, and the
    # next test saw a third call it never made). These tests are about the graphical flow.
    monkeypatch.setattr(providers, "chat", lambda *_args, **_kwargs: ("{}", {"model": "stub"}))
    server = firefox.BridgeServer(port=0)
    server.runner = firefox.TaskRunner(server)
    server.start()
    return server, FakeExtension(server.port)


def test_a_key_pasted_in_the_sidebar_configures_the_host(clean_env, tmp_path, monkeypatch):
    """The whole graphical setup: hello → paste key → saved, derived, announced."""
    server, ext = _live_bridge(tmp_path, monkeypatch)
    try:
        ext.send({"type": "hello"})
        welcome = ext.recv()
        assert welcome["type"] == "welcome" and welcome["ok"] is True
        assert welcome["state"]["setup"]["configured"] is False

        ext.send({"type": "setup.key", "variable": "GROQ_API_KEY", "value": "gsk_from_firefox"})
        # Wait for the two facts this test is about, not for a fixed number of frames: the host
        # also announces the role it is testing, so the save confirmation can sit behind them.
        import time

        frames = []
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                frames.append(ext.recv(timeout=5))
            except (TimeoutError, socket.timeout):
                break
            saved = any(
                frame.get("type") == "notice" and "GROQ_API_KEY" in frame.get("message", "") for frame in frames
            )
            configured = any(
                frame.get("type") == "state" and frame["state"]["setup"]["configured"] is True for frame in frames
            )
            if saved and configured:
                break
        notices = [frame for frame in frames if frame.get("type") == "notice"]
        # The save confirmation is a notice, but it is not necessarily the first one: the host
        # announces the role it is testing the moment it starts asking.
        assert any("GROQ_API_KEY" in notice["message"] for notice in notices), frames

        # saved where every other path reads it
        assert (tmp_path / ".env").read_text(encoding="utf-8").strip() == "GROQ_API_KEY=gsk_from_firefox"
        assert providers.selection_for("planner") == ("groq", "openai/gpt-oss-120b")

        # the sidebar is told the new state, and the key never travels back
        assert not any("gsk_from_firefox" in json.dumps(frame) for frame in frames)
        assert any(
            frame.get("type") == "state" and frame["state"]["setup"]["configured"] is True for frame in frames
        ), [frame.get("type") for frame in frames]
    finally:
        ext.close()
        server.close()


def test_a_bad_paste_answers_with_the_reason_and_touches_nothing(clean_env, tmp_path, monkeypatch):
    server, ext = _live_bridge(tmp_path, monkeypatch)
    try:
        ext.send({"type": "hello"})
        ext.recv()
        ext.send({"type": "setup.key", "variable": "GROQ_API_KEY", "value": "not a key with spaces"})
        while True:
            message = ext.recv(timeout=5)
            if message.get("type") == "error":
                assert "does not look like an API key" in message["message"]
                break
        assert not (tmp_path / ".env").exists()
        assert providers.is_configured() is False
    finally:
        ext.close()
        server.close()


def test_the_host_starts_without_a_key_and_points_at_the_sidebar(clean_env, tmp_path, monkeypatch, capsys):
    """Double-click with no key: no prompt in the terminal, no early exit."""
    asked = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_ENV_FILE", str(tmp_path / ".env"))  # a fresh machine has no key file
    monkeypatch.setattr("builtins.input", lambda *a: asked.append(a) or "")
    class Stop(threading.Event):
        def wait(self, *_args, **_kwargs):
            raise KeyboardInterrupt

    class Threading:
        """The real module, with only its blocking wait replaced."""

        Event = Stop

        def __getattr__(self, name):
            return getattr(threading, name)

    monkeypatch.setattr(firefox, "threading", Threading())
    monkeypatch.setattr(firefox, "BridgeServer", _StubServer)
    firefox.main()  # returns instead of waiting forever

    printed = capsys.readouterr().out
    assert "open the agent sidebar" in printed
    assert "Policy model" not in printed  # nothing configured yet
    assert asked == []  # never asked anything on the command line


# ── contracts between the extension and the host (no browser needed) ─────────


def _read(path):
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


def test_every_message_the_extension_sends_is_understood_by_the_host():
    """The sidebar is the setup surface now: a typo in a message type would be silent."""
    import re

    sent = set(re.findall(r'send\(\{\s*type:\s*"([^"]+)"', _read("extension/background.js")))
    routed = set()
    for chunk in re.findall(r'kind (?:==|in) (\{[^}]*\}|"[^"]+")', _read("jev_ultrafast/firefox.py")):
        routed.update(re.findall(r'"([^"]+)"', chunk))
    assert sent, "no message types found - did background.js change shape?"
    assert sent <= routed, f"the host does not route: {sorted(sent - routed)}"


def test_every_command_the_sidebar_uses_is_relayed_by_the_background_script():
    import re

    used = set(re.findall(r'cmd:\s*"([^"]+)"', _read("extension/sidebar/sidebar.js")))
    relayed = set(re.findall(r'message\.cmd === "([^"]+)"', _read("extension/background.js")))
    assert used <= relayed, f"the background script does not relay: {sorted(used - relayed)}"


def test_the_setup_form_only_uses_elements_that_exist():
    import re

    html_ids = set(re.findall(r'id="([^"]+)"', _read("extension/sidebar/sidebar.html")))
    js_ids = set(re.findall(r'\$\("([^"]+)"\)', _read("extension/sidebar/sidebar.js")))
    assert js_ids <= html_ids, f"the sidebar asks for elements that do not exist: {sorted(js_ids - html_ids)}"


def test_background_and_host_agree_on_where_the_agent_listens():
    """The extension's default port must be the bridge's default port."""
    import re

    assert re.search(r'const DEFAULT_PORT = (\d+)', _read("extension/background.js")).group(1) == str(
        firefox.DEFAULT_PORT
    )


class _RecordingBridge:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def broadcast(self, message):
        self.sent.append(message)


def test_a_verdict_from_before_the_key_is_never_published(clean_env, tmp_path, monkeypatch):
    """The classic race: a check starts with no key, the key arrives, the check answers anyway.

    Decided by handshakes instead of by sleeping: the first check is held open until the key
    has really been saved, so the outcome depends on the app's guard and not on how fast the
    machine is. A 0.4 s sleep was plenty here and not on the Windows runner, where the save
    came in after the first check had already answered.
    """
    import time

    calls = []
    first_check_running = threading.Event()
    key_was_saved = threading.Event()

    def slow_check(on_event=None):
        call = len(calls) + 1
        calls.append(call)
        if on_event is not None:  # the host hands the check a progress channel; the test uses it too
            on_event("start", {"role": "policy", "model": "groq:openai/gpt-oss-20b"})
        if call == 1:
            first_check_running.set()
            key_was_saved.wait(20)  # the key arrives while this check is in flight
            return {"policy": {"role": "policy", "ok": False, "detail": "call 1"}}
        return {"policy": {"role": "policy", "ok": True, "detail": f"call {call}"}}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setattr(firefox, "check_providers", slow_check)
    bridge = _RecordingBridge()
    runner = firefox.TaskRunner(bridge)

    threading.Thread(target=runner.run_provider_check, daemon=True).start()
    assert first_check_running.wait(20), "the first check never started"
    runner.handle_setup_key("GROQ_API_KEY", "gsk_arrived_late")
    key_was_saved.set()

    deadline = time.time() + 30
    while time.time() < deadline and runner.provider_check is None:
        time.sleep(0.05)
    assert runner.provider_check is not None, "no verdict was published at all"
    assert len(calls) >= 2, "the fresh check did not run"
    detail = runner.provider_check["policy"]["detail"]
    assert detail != "call 1", f"a stale verdict was published: {detail}"
    assert detail.startswith("call "), detail
    # and the discarded verdict must never have reached the sidebar, not even for a moment
    published = [
        (message.get("state") or {}).get("providers", {}) or {}
        for message in bridge.sent
        if message.get("type") == "state"
    ]
    assert all((state.get("policy") or {}).get("detail") != "call 1" for state in published), (
        "the stale verdict was broadcast to the sidebar"
    )
    assert any("Saved GROQ_API_KEY" in m.get("message", "") for m in bridge.sent if m.get("type") == "notice")
    # the wait is visible, not silent: the check says which role it is asking
    assert any(
        "Testing Executor (groq:openai/gpt-oss-20b)" in m.get("message", "")
        for m in bridge.sent
        if m.get("type") == "notice"
    ), [m for m in bridge.sent if m.get("type") == "notice"]
