"""Contracts for the MVP additions to the Firefox bridge: approvals, model
handlers, and the orchestrated task mode. No browser, no paid APIs."""

import threading
import time

import pytest

from jev_ultrafast import discovery, firefox, parameters
from tests.test_firefox import FakeExtension


@pytest.fixture
def bridge():
    server = firefox.BridgeServer(port=0)
    server.start()
    yield server
    server.close()


class MockBridge:
    """Records sends/broadcasts so threaded handlers can be asserted on."""

    def __init__(self):
        self.sent = []
        self._lock = threading.Lock()

    def send(self, message):
        with self._lock:
            self.sent.append(message)

    def broadcast(self, message):
        self.send(message)

    @property
    def messages(self):
        with self._lock:
            return list(self.sent)


class FailingBridge:
    def send(self, message):
        raise firefox.BridgeError("not connected")

    def broadcast(self, message):
        raise firefox.BridgeError("not connected")


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    from jev_ultrafast import permissions

    monkeypatch.setattr(parameters, "CONFIG_PATH", tmp_path / "model-config.json")
    monkeypatch.setattr(discovery, "REGISTRY_PATH", tmp_path / "model-registry.json")
    monkeypatch.setattr(permissions, "PERMISSIONS_PATH", tmp_path / "permissions.json")
    monkeypatch.setattr("jev_ultrafast.neko.SESSIONS_PATH", tmp_path / "neko-sessions.json")
    for name in (
        "PLANNER_PROVIDER", "PLANNER_MODEL", "POLICY_PROVIDER", "POLICY_MODEL",
        "TEXT_MODEL_PROVIDER", "TEXT_MODEL", "TYPESAFE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JEV_LAYA", "off")  # hermetic: keyword routing only
    monkeypatch.setattr(firefox, "check_providers", lambda *_a, **_k: {"planner": {"ok": True}})
    yield


# ── ApprovalGate (MVP-4) ─────────────────────────────────────────────────────


def test_approval_gate_round_trip(bridge):
    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    ext.recv()  # welcome
    gate = firefox.ApprovalGate(bridge)
    verdicts = []

    def ask():
        verdicts.append(gate.request("rm notes.txt"))

    thread = threading.Thread(target=ask)
    thread.start()

    message = ext.recv(timeout=5)
    assert message["type"] == "approval_request"
    assert message["command"] == "rm notes.txt"
    assert gate.respond(message["id"], True) is True
    thread.join(timeout=5)
    assert verdicts == [True]


def test_approval_gate_denies_on_timeout():
    gate = firefox.ApprovalGate(MockBridge(), timeout=0.05)
    assert gate.request("dangerous-command") is False


def test_approval_gate_fails_closed_without_sidebar():
    gate = firefox.ApprovalGate(FailingBridge(), timeout=0.05)
    assert gate.request("dangerous-command") is False


def test_approval_gate_ignores_unknown_ids():
    gate = firefox.ApprovalGate(MockBridge())
    assert gate.respond("bogus-id", True) is False


# ── model & parameter handlers (MVP-1) ───────────────────────────────────────


def test_model_select_updates_env_config_and_state():
    import os

    runner = firefox.TaskRunner(MockBridge())
    runner.handle_model_select({"role": "text", "provider": "groq", "model": "openai/gpt-oss-20b"})
    assert os.environ["TEXT_MODEL_PROVIDER"] == "groq"
    assert os.environ["TEXT_MODEL"] == "openai/gpt-oss-20b"
    saved = parameters.load_config()
    assert saved["models"]["text"] == {"provider": "groq", "model": "openai/gpt-oss-20b"}
    states = [m["state"] for m in runner.bridge.messages if m.get("type") == "state"]
    assert states[-1]["selection"]["text"]["model"] == "openai/gpt-oss-20b"
    assert states[-1]["schema"]["roles"][0]["key"] == "planner"
    assert "fast" in states[-1]["presets"]


def test_model_select_rejects_bad_payloads_without_crashing():
    runner = firefox.TaskRunner(MockBridge())
    runner.handle_model_select({"role": "hacker", "provider": "x", "model": "y"})
    errors = [m for m in runner.bridge.messages if m.get("type") == "error"]
    assert errors and "Could not switch model" in errors[0]["message"]


def test_policy_can_leave_and_return_to_typesafe(monkeypatch):
    import os

    monkeypatch.setenv("TYPESAFE_API_KEY", "tsk-1")
    runner = firefox.TaskRunner(MockBridge())
    assert runner.current_state()["policy_builtin"] is True

    runner.handle_model_select({"role": "policy", "provider": "groq", "model": "openai/gpt-oss-20b"})
    assert "TYPESAFE_API_KEY" not in os.environ  # the UI choice overrides .env
    assert os.environ["POLICY_PROVIDER"] == "groq"

    runner.handle_model_select({"role": "policy", "provider": "", "model": ""})
    assert os.environ["TYPESAFE_API_KEY"] == "tsk-1"  # restored for this session
    assert "POLICY_PROVIDER" not in os.environ
    assert "policy" not in (parameters.load_config().get("models") or {})


def test_params_set_applies_presets_and_role_params():
    import os

    runner = firefox.TaskRunner(MockBridge())
    runner.handle_params_set({"preset": "deep"})
    assert os.environ["PLANNER_REASONING"] == "high"
    runner.handle_params_set({"role": "text", "params": {"temperature": 0.3}})
    assert os.environ["TEXT_MODEL_TEMPERATURE"] == "0.3"
    runner.handle_params_set({"role": "text", "params": {"temperature": None}})
    assert "TEXT_MODEL_TEMPERATURE" not in os.environ
    runner.handle_params_set({"preset": "no-such-preset"})
    errors = [m for m in runner.bridge.messages if m.get("type") == "error"]
    assert errors and "Could not apply parameters" in errors[0]["message"]


def test_handle_models_sends_cache_then_refresh(monkeypatch):
    runner = firefox.TaskRunner(MockBridge())
    cached = {"fetchedAt": time.time(), "providers": {"groq": {"ok": True, "models": [{"id": "m1"}]}}}
    fresh = {"fetchedAt": time.time(), "providers": {"groq": {"ok": True, "models": [{"id": "m1"}, {"id": "m2"}]}}}
    monkeypatch.setattr(discovery, "_load_registry", lambda: cached)
    monkeypatch.setattr(discovery, "discover", lambda refresh=False: fresh)

    runner.handle_models(refresh=False)
    assert wait_until(lambda: len([m for m in runner.bridge.messages if m.get("type") == "models"]) == 2)
    sent = [m for m in runner.bridge.messages if m.get("type") == "models"]
    assert sent[0]["registry"] == cached  # instant answer from the cache
    assert sent[1]["registry"] == fresh  # background refresh


def test_handle_models_without_cache_announces_loading(monkeypatch):
    runner = firefox.TaskRunner(MockBridge())
    monkeypatch.setattr(discovery, "_load_registry", lambda: None)
    monkeypatch.setattr(discovery, "discover", lambda refresh=False: {"fetchedAt": 1, "providers": {}})
    runner.handle_models(refresh=False)
    assert wait_until(lambda: len([m for m in runner.bridge.messages if m.get("type") == "models"]) == 2)
    sent = [m for m in runner.bridge.messages if m.get("type") == "models"]
    assert sent[0].get("loading") is True and sent[0]["registry"] is None
    assert sent[1]["registry"]["providers"] == {}


# ── orchestrated task mode (MVP-3) ───────────────────────────────────────────


def test_start_routes_browser_goals_to_the_fast_loop(monkeypatch):
    runner = firefox.TaskRunner(MockBridge())
    calls = []
    monkeypatch.setattr(runner, "_run", lambda *args: calls.append(args))
    monkeypatch.setattr(
        firefox.TaskRunner, "_run_orchestrated", lambda self, *args: calls.append(("orchestrated", *args))
    )
    runner.start("Find flights on this page", "https://example.com", 7)
    assert calls and calls[0][0] != "orchestrated"
    runner._lock.release()  # the mocked _run did not release what start() acquired


def test_orchestrated_run_reports_state_and_final(monkeypatch, tmp_path):
    from jev_ultrafast import orchestrator
    from jev_ultrafast.tools import ToolBox

    def fake_orchestration(goal, toolbox, on_step=None, max_steps=25, on_delta=None):
        assert isinstance(toolbox, ToolBox)
        assert toolbox.browser_runner is not None
        on_step({"step": 1, "tool": "write_file", "args": {"path": "a.txt"}, "result": "Wrote 2 characters"})
        on_step({"step": 2, "tool": None, "final": "done: created a.txt"})
        return {"final": "done: created a.txt", "steps": [], "usage": {"input_tokens": 5}, "latency_ms": 12}

    monkeypatch.setattr(orchestrator, "run_orchestration", fake_orchestration)
    runner = firefox.TaskRunner(MockBridge(), workspace=tmp_path / "ws")
    runner.start("create a python script a.txt with hi", "https://example.com", 7)
    assert wait_until(lambda: runner.orchestrated and runner.orchestrated.get("status") == "done")

    state = runner.current_state()
    assert state["mode"] == "orchestrated"
    assert state["final"] == "done: created a.txt"
    assert state["log"][0]["tool"] == "write_file"
    assert state["skills"]  # the mission matched at least one skill
    assert "browser" not in state  # no browser sub-run happened
    assert not runner._lock.locked()  # released for the next task


def test_orchestrated_run_survives_crashes(monkeypatch, tmp_path):
    from jev_ultrafast import orchestrator

    def exploding(goal, toolbox, on_step=None, max_steps=25, on_delta=None):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(orchestrator, "run_orchestration", exploding)
    runner = firefox.TaskRunner(MockBridge(), workspace=tmp_path / "ws")
    runner.start("create a file and run away", "https://example.com", 7)
    assert wait_until(lambda: runner.orchestrated and runner.orchestrated.get("status") == "error")
    assert "planner exploded" in runner.last_error
    assert not runner._lock.locked()
    errors = [m for m in runner.bridge.messages if m.get("type") == "error"]
    assert errors and "planner exploded" in errors[0]["message"]


def test_orchestrated_run_over_the_real_bridge(bridge, monkeypatch, tmp_path):
    """The full loop over a real socket: broadcasts reach the extension."""
    from jev_ultrafast import orchestrator

    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    welcome = ext.recv()
    assert welcome["type"] == "welcome" and welcome["ok"] is True
    received = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                message = ext.recv(timeout=0.2)
            except (TimeoutError, OSError, ConnectionError):
                continue
            received.append(message)
            if "id" in message:  # answer host commands
                ext.send({"id": message["id"], "ok": True, "result": {}})

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def fake_orchestration(goal, toolbox, on_step=None, max_steps=25, on_delta=None):
        on_step({"step": 1, "tool": "list_files", "args": {}, "result": "(empty)"})
        return {"final": "listed", "steps": [], "usage": {}, "latency_ms": 3}

    monkeypatch.setattr(orchestrator, "run_orchestration", fake_orchestration)
    runner = firefox.TaskRunner(bridge, workspace=tmp_path / "ws")
    bridge.runner = runner
    runner.start("research and create a project", "https://example.com", 7)
    assert wait_until(lambda: runner.orchestrated and runner.orchestrated.get("status") == "done")
    # the final broadcast may still be in flight over the socket: wait for it
    assert wait_until(lambda: any(
        m.get("type") == "state" and (m.get("state") or {}).get("final") == "listed" for m in received
    ))
    stop.set()
    thread.join(timeout=2)

    states = [m["state"] for m in received if m.get("type") == "state"]
    assert states and states[-1]["mode"] == "orchestrated"
    assert states[-1]["final"] == "listed"
    assert states[-1]["log"][0]["tool"] == "list_files"


# ── permission center + sandbox over the bridge (MVP-5) ──────────────────────


def read_until(ext, predicate, timeout=5.0):
    """Read bridge messages until one satisfies the predicate (or time out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = ext.recv(timeout=max(0.1, deadline - time.monotonic()))
        except (TimeoutError, OSError):
            return None
        if predicate(message):
            return message
    return None


def test_permissions_set_round_trip(bridge, monkeypatch, tmp_path):
    from jev_ultrafast import permissions

    monkeypatch.setattr(permissions, "PERMISSIONS_PATH", tmp_path / "permissions.json")
    bridge.runner = firefox.TaskRunner(bridge)
    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    ext.recv()  # welcome

    ext.send({"type": "permissions.set", "scope": "downloads", "level": "deny"})
    state = read_until(
        ext, lambda m: m.get("type") == "state" and m["state"]["permissions"]["levels"]["downloads"] == "deny"
    )
    assert state is not None, "the sidebar never learned about the new level"
    assert permissions.load()["downloads"] == "deny"

    ext.send({"type": "permissions.set", "scope": "telepathy", "level": "allow"})
    assert read_until(ext, lambda m: m.get("type") == "error" and "Permission error" in m.get("message", ""))

    ext.send({"type": "permissions.reset"})
    assert read_until(
        ext, lambda m: m.get("type") == "state" and m["state"]["permissions"]["levels"]["downloads"] == "allow"
    )


def test_browser_target_switch_round_trip(bridge, monkeypatch, tmp_path):
    from jev_ultrafast import permissions

    monkeypatch.setattr(permissions, "PERMISSIONS_PATH", tmp_path / "permissions.json")
    monkeypatch.setattr("jev_ultrafast.neko.SESSIONS_PATH", tmp_path / "neko-sessions.json")
    bridge.runner = firefox.TaskRunner(bridge)
    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    ext.recv()

    ext.send({"type": "mode.set", "mode": "sandbox"})
    assert read_until(ext, lambda m: m.get("type") == "state" and m["state"]["browserMode"] == "sandbox")
    ext.send({"type": "mode.set", "mode": "telepathy"})
    assert read_until(ext, lambda m: m.get("type") == "error" and "Unknown browser mode" in m.get("message", ""))


def test_sandbox_start_is_gated_by_the_browser_scope(bridge, monkeypatch, tmp_path):
    from jev_ultrafast import permissions

    monkeypatch.setattr(permissions, "PERMISSIONS_PATH", tmp_path / "permissions.json")
    permissions.set_level("browser", "deny")
    bridge.runner = firefox.TaskRunner(bridge)
    ext = FakeExtension(bridge.port)
    ext.send({"type": "hello"})
    ext.recv()

    ext.send({"type": "sandbox.start"})
    error = read_until(
        ext, lambda m: m.get("type") == "error" and "browser scope is set to deny" in m.get("message", "")
    )
    assert error is not None
    assert bridge.runner.sandbox_session is None


def test_sandbox_state_is_always_reported(bridge, monkeypatch, tmp_path):
    monkeypatch.setattr("jev_ultrafast.neko.SESSIONS_PATH", tmp_path / "neko-sessions.json")
    bridge.runner = firefox.TaskRunner(bridge)
    state = bridge.runner.current_state()
    assert "sandbox" in state and "available" in state["sandbox"]
    assert state["browserMode"] == "live"


def test_models_ui_receives_the_per_role_schema():
    runner = firefox.TaskRunner(MockBridge())
    state = runner.current_state()
    assert state["schema"]["roles"][0]["key"] == "planner"
    assert set(state["schema"]["parameters"])  # the whole vocabulary
    # one resolved surface per role, riding with that role's selection
    for role in ("planner", "policy", "text"):
        surface = state["selection"][role]["schema"]
        assert "parameters" in surface and "schemaId" in surface


def test_model_probe_reports_and_reruns_the_schema(monkeypatch):
    """The sidebar's "Probe model" button: one live check, then a fresh schema."""
    import os

    from jev_ultrafast import discovery

    os.environ["POLICY_PROVIDER"] = "nvidia"
    os.environ["POLICY_MODEL"] = "z-ai/glm-5.3"
    runner = firefox.TaskRunner(MockBridge())
    monkeypatch.setattr(
        discovery,
        "probe_model",
        lambda provider, model_id: {
            "provider": provider, "model": model_id, "schemaId": "glm-v1",
            "verified": ["temperature"], "unsupported": ["seed"], "error": None,
        },
    )
    runner.handle_model_probe({"role": "policy"})
    for _ in range(50):
        reports = [m for m in runner.bridge.messages if m.get("type") == "probe" and not m.get("loading")]
        if reports:
            break
        time.sleep(0.02)
    assert reports and reports[0]["report"]["unsupported"] == ["seed"]

    runner.handle_model_probe({"role": "nobody"})
    assert any("Unknown role" in m.get("message", "") for m in runner.bridge.messages if m.get("type") == "error")


def test_switching_models_clears_parameters_the_new_one_lacks():
    import os

    os.environ.pop("POLICY_TOP_P", None)
    runner = firefox.TaskRunner(MockBridge())
    runner.handle_model_select({"role": "policy", "provider": "nvidia", "model": "z-ai/glm-5.3"})
    runner.handle_params_set({"role": "policy", "params": {"top_p": 0.8}})
    assert os.environ["POLICY_TOP_P"] == "0.8"
    runner.handle_model_select({"role": "policy", "provider": "nvidia", "model": "moonshotai/kimi-k3"})
    assert "POLICY_TOP_P" not in os.environ
    notices = [m for m in runner.bridge.messages if m.get("type") == "notice"]
    assert notices and "top_p" in notices[-1]["message"]
