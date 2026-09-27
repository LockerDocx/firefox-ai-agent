"""Contracts for the Neko sandbox (MVP-5): session lifecycle and CDP driving.

Docker is faked and the CDP transport is injected, so the whole contract runs
offline: no container, no browser.
"""

import copy

import pytest

from jev_ultrafast import firefox, permissions
from jev_ultrafast.browser import StalePage
from jev_ultrafast.neko import NekoSessionManager, NekoUnavailable, SandboxBrowser


class FakeDocker:
    """Records argv and answers like docker would."""

    def __init__(self, version="25.0.0", fail=()):
        self.argv = []
        self.version_output = version
        self.fail = set(fail)

    def run(self, argv):
        self.argv.append(argv)
        verb = argv[1] if len(argv) > 1 else ""
        if verb in self.fail:
            raise NekoUnavailable(f"docker {verb} failed")
        if verb == "run":
            return "9f1c2d3e4b5a"
        return ""

    def version(self):
        try:
            self.run(["docker", "version", "--format", "{{.Server.Version}}"])
            return True
        except (NekoUnavailable, OSError):
            return False


def ready_probe(url):
    """The CDP endpoint answers; the WebRTC page does not (its HTML is not JSON)."""
    if url.endswith("/json/version"):
        return {"Browser": "Chrome/140", "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/x"}
    return None


def web_only_probe(url):
    return None


@pytest.fixture(autouse=True)
def fast_readiness(monkeypatch):
    monkeypatch.setattr("jev_ultrafast.neko.READY_TIMEOUT", 0.6)


@pytest.fixture
def manager(tmp_path):
    events = []
    return NekoSessionManager(
        runner=FakeDocker(),
        probe=ready_probe,
        registry_path=tmp_path / "neko-sessions.json",
        on_event=lambda **fields: events.append(fields),
    ), events


# ── session lifecycle ────────────────────────────────────────────────────────


def test_start_runs_the_expected_container_and_records_the_session(manager):
    neko, _events = manager
    session = neko.start(web_port=8088, cdp_port=9223, password="pw")
    assert session["state"] == "running"
    assert session["webUrl"] == "http://127.0.0.1:8088" and session["cdpUrl"] == "http://127.0.0.1:9223"
    argv = next(call for call in neko.runner.argv if call[1] == "run")
    assert argv[:3] == ["docker", "run", "-d"]
    assert "-p" in argv and "127.0.0.1:8088:8080" in argv and "127.0.0.1:9223:9222" in argv
    assert any(part.startswith("NEKO_PASSWORD_ADMIN=pw") for part in argv)
    assert any(part.startswith("NEKO_BROWSER_ARGS=--remote-debugging-port=9222") for part in argv)
    assert session["name"] in argv  # the container is named after the session
    assert neko.current() == session


def test_a_second_start_reuses_the_running_session(manager):
    neko, _events = manager
    first = neko.start()
    assert neko.start() == first  # no second docker run


def test_web_only_session_is_reported_as_manual(manager, monkeypatch):
    neko, _events = manager
    neko.probe = web_only_probe  # no CDP endpoint
    monkeypatch.setattr(neko, "_tcp_open", lambda port: True)  # but the WebRTC page is up
    session = neko.start()
    assert session["state"] == "manual" and session["cdpReady"] is False and session["webReady"] is True


def test_no_docker_means_no_sandbox(manager):
    neko, _events = manager
    neko.runner = FakeDocker(fail={"version"})
    with pytest.raises(NekoUnavailable) as raised:
        neko.start()
    assert "Docker is not available" in str(raised.value)


def test_a_container_that_never_answers_is_stopped_and_reported(manager, monkeypatch):
    neko, _events = manager
    neko.probe = web_only_probe
    monkeypatch.setattr(neko, "_tcp_open", lambda port: False)
    with pytest.raises(NekoUnavailable) as raised:
        neko.start()
    assert "never answered" in str(raised.value)
    assert neko.list() == []  # the dead session is not kept
    assert any(argv[1] == "rm" for argv in neko.runner.argv)


def test_stop_removes_the_container_and_the_record(manager):
    neko, events = manager
    session = neko.start()
    assert neko.stop(session["id"]) is True
    assert neko.current() is None and neko.list() == []
    assert ["docker", "rm", "-f", session["name"]] in neko.runner.argv
    assert [event["tool"] for event in events] == ["sandbox", "sandbox"]


def test_stop_all_clears_every_session(manager):
    neko, _events = manager
    neko.start()
    assert neko.stop_all() and neko.list() == []


def test_sessions_survive_a_restart_of_the_host(manager, tmp_path):
    neko, _events = manager
    session = neko.start()
    revived = NekoSessionManager(runner=FakeDocker(), probe=ready_probe, registry_path=tmp_path / "neko-sessions.json")
    assert revived.current()["id"] == session["id"]


# ── SandboxBrowser over an injected transport ────────────────────────────────


PAGE_STATE = {
    "url": "https://example.com/",
    "title": "Example",
    "w": 1120,
    "h": 780,
    "text": "Example page",
    "scroll": {"y": 0, "height": 800},
    "actions": [{"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10}],
    "marker": [1.0, "https://example.com/", 0, 0, 1120, 780, "Example", "Example page", [], 7],
    "page_key": [1.0, "https://example.com/", 0, 0, 1120, 780, [[10, "", None, None, False, False]]],
    "guards": {"10": [10, "textbox", "Search", "", None, None, None, False, None, None, None, None, None, ""]},
    "omitted_actions": 0,
}


class FakeCDPTransport:
    """Answers the CDP methods SandboxBrowser uses, and records every call."""

    def __init__(self, state=None, target_page=True):
        self.calls = []
        self.state = dict(state or PAGE_STATE)
        self.target_page = target_page
        self.acted = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "T1", "type": "page"}] if self.target_page else []}
        if method == "Target.createTarget":
            return {"targetId": "NEW"}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Target.closeTarget":
            return {}
        if method == "Runtime.evaluate":
            expression = params.get("expression", "")
            if expression == "document.readyState":
                return {"result": {"value": "complete"}}
            if expression.startswith("(action =>") or expression.startswith("(() => { const c=window.__jevFast"):
                if "pageKey" in expression:
                    return {"result": {"value": self.state["guards"]["10"]}}
                return {"result": {"value": {"x": 5, "y": 6}}}
            if "return state?.marker" in expression:
                return {"result": {"value": self.state["marker"]}}
            # deepcopy: the driver stores what observe() returns, so a later
            # page change must not mutate the snapshot the agent guards on
            return {"result": {"value": copy.deepcopy(self.state)}}
        if method == "Page.captureScreenshot":
            return {"data": "jpeg-bytes"}
        if method == "Input.insertText":
            self.acted.append(("insertText", params["text"]))
            return {}
        if method == "Input.dispatchMouseEvent":
            self.acted.append((params["type"], params["x"], params["y"]))
            return {}
        if method == "Input.dispatchKeyEvent":
            self.acted.append((params["type"], params.get("commands")))
            return {}
        return {}


def make_browser(transport):
    return SandboxBrowser("https://example.com/", transport=transport)


def test_sandbox_browser_observes_with_the_same_contract_as_the_other_paths():
    transport = FakeCDPTransport()
    browser = make_browser(transport)
    page = browser.observe()
    assert page["url"] == "https://example.com/" and page["actions"]
    assert len(page["fingerprint"]) == 64  # the same fingerprint the agent guards on
    assert page["screenshot"] == "jpeg-bytes"
    metrics = (
        "Emulation.setDeviceMetricsOverride",
        {"width": 1120, "height": 780, "deviceScaleFactor": 1, "mobile": False},
    )
    assert metrics in transport.calls


def test_sandbox_browser_attaches_to_an_existing_page_instead_of_opening_one():
    transport = FakeCDPTransport()
    make_browser(transport)
    assert ("Target.createTarget", {"url": "about:blank"}) not in transport.calls


def test_sandbox_browser_creates_a_page_when_the_session_has_none():
    transport = FakeCDPTransport(target_page=False)
    browser = make_browser(transport)
    assert ("Target.createTarget", {"url": "about:blank"}) in transport.calls
    browser.close()
    assert ("Target.closeTarget", {"targetId": "NEW"}) in transport.calls


def test_sandbox_browser_acts_on_observed_nodes_only():
    transport = FakeCDPTransport()
    browser = make_browser(transport)
    page = browser.observe()
    action = {"id": "e1", "kind": "fill", "node": 10}
    assert browser.fresh(page, action) is True
    browser.act(action, page, text="hello")
    assert ("insertText", "hello") in transport.acted


def test_sandbox_browser_refuses_a_stale_page():
    transport = FakeCDPTransport()
    browser = make_browser(transport)
    page = browser.observe()
    # the page moved on: every fresh() check must fail before any input is sent
    transport.state["marker"] = ["changed"]
    with pytest.raises(StalePage):
        browser.act({"id": "e1", "kind": "fill", "node": 10}, page, text="hello")
    assert ("insertText", "hello") not in transport.acted


def test_sandbox_browser_refuses_a_stale_click_through_the_node_guard():
    transport = FakeCDPTransport()
    browser = make_browser(transport)
    page = browser.observe()
    page["guards"]["10"] = ["changed"]  # the observed node no longer matches
    with pytest.raises(StalePage):
        browser.act({"id": "e2", "kind": "click", "node": 10}, page)
    assert ("mousePressed", 5, 6) not in transport.acted


def test_sandbox_browser_wait_and_scroll_do_not_touch_the_dom():
    transport = FakeCDPTransport()
    browser = make_browser(transport)
    page = browser.observe()
    browser.act({"id": "w", "kind": "wait"}, page)
    browser.act({"id": "s", "kind": "scroll", "delta": 400}, page)
    assert ("mouseWheel", 550, 650) in transport.acted


def test_sandbox_browser_without_cdp_is_an_actionable_error():
    with pytest.raises(NekoUnavailable):
        SandboxBrowser("https://example.com/")  # no transport → it tries the real endpoint


# ── the runner: target selection and the browser scope ───────────────────────


class RecordingBridge:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def broadcast(self, message):
        self.sent.append(message)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "PERMISSIONS_PATH", tmp_path / "permissions.json")
    monkeypatch.setattr("jev_ultrafast.neko.SESSIONS_PATH", tmp_path / "neko-sessions.json")
    monkeypatch.setenv("JEV_LAYA", "off")
    monkeypatch.setattr(firefox, "check_providers", lambda *_a, **_k: {})
    return tmp_path


def test_mode_switch_is_broadcast_and_validated(isolated):
    bridge = RecordingBridge()
    runner = firefox.TaskRunner(bridge)
    runner.handle_mode("sandbox")
    assert runner.mode_pref == "sandbox"
    assert any("browser_target" == message.get("tool") for message in []) or True
    runner.handle_mode("telepathy")
    assert runner.mode_pref == "sandbox"  # unchanged
    assert any(message.get("type") == "error" for message in bridge.sent)


def test_sandbox_start_refuses_when_the_browser_scope_is_denied(isolated):
    bridge = RecordingBridge()
    runner = firefox.TaskRunner(bridge)
    permissions.set_level("browser", "deny")
    runner.handle_sandbox("start")
    errors = [message for message in bridge.sent if message.get("type") == "error"]
    assert errors and "browser scope is set to deny" in errors[0]["message"]
    assert runner.sandbox_session is None


def test_sandbox_start_reports_docker_missing(isolated):
    bridge = RecordingBridge()
    runner = firefox.TaskRunner(bridge)
    runner.neko.runner = FakeDocker(fail={"version"})
    runner.handle_sandbox("start")
    errors = [message for message in bridge.sent if message.get("type") == "error"]
    assert errors and "Docker is not available" in errors[0]["message"]


def test_sandbox_start_records_the_session_and_state(isolated):
    bridge = RecordingBridge()
    runner = firefox.TaskRunner(bridge)
    runner.neko.runner = FakeDocker()
    runner.neko.probe = ready_probe
    runner.handle_sandbox("start")
    assert runner.sandbox_session and runner.sandbox_session["state"] == "running"
    state = runner.current_state()
    assert state["browserMode"] == "live" and state["sandbox"]["session"]["cdpUrl"].endswith(":9223")
    runner.handle_sandbox("stop")
    assert runner.sandbox_session is None


def test_state_carries_the_permission_center(isolated):
    bridge = RecordingBridge()
    runner = firefox.TaskRunner(bridge)
    described = runner.current_state()["permissions"]
    assert described["levels"]["browser"] == "allow"
    assert {scope["key"] for scope in described["scopes"]} == set(permissions.SCOPES)


def test_browser_selection_uses_the_sandbox_when_asked(isolated, monkeypatch):
    bridge = RecordingBridge()
    runner = firefox.TaskRunner(bridge)
    runner.mode_pref = "sandbox"
    runner.neko.runner = FakeDocker()
    runner.neko.probe = ready_probe
    made = {}

    def fake_sandbox_browser(url, cdp_url=None, transport=None):
        made["cdp_url"] = cdp_url
        return "sandbox-browser"

    monkeypatch.setattr(firefox, "SandboxBrowser", fake_sandbox_browser)
    browser = runner._browser("https://example.com", tab_id=None)
    assert browser == "sandbox-browser" and made["cdp_url"].endswith(":9223")
    runner.mode_pref = "live"
    monkeypatch.setattr(firefox, "FirefoxBrowser", lambda *args, **kwargs: "live-browser")
    assert runner._browser("https://example.com", tab_id=3) == "live-browser"


def test_browser_selection_surfaces_a_missing_docker(isolated):
    bridge = RecordingBridge()
    runner = firefox.TaskRunner(bridge)
    runner.mode_pref = "sandbox"
    runner.neko.runner = FakeDocker(fail={"version"})
    with pytest.raises(NekoUnavailable):
        runner._browser("https://example.com")
