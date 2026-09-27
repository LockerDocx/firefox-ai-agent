"""Native messaging: Firefox starts the host itself - no window, no port, no double click.

The frame format (4-byte length, UTF-8 JSON) and the manifest locations are
Mozilla's, not ours: these tests pin the exact wire protocol and the exact paths
the browser looks in, because a mismatch here fails silently in the browser.
"""

import io
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from jev_ultrafast import firefox, native_setup

ROOT = Path(__file__).parent.parent


WINDOWS = os.name == "nt"


def isolate_user(monkeypatch, tmp_path):
    """Point the browser's per-user folders at tmp_path on any platform.

    Windows reads APPDATA/USERPROFILE and the registry; POSIX reads HOME. The
    registry write is stubbed on Windows so running the suite never touches the
    machine it runs on, while still asserting what would be written.
    """
    written = {}
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    if WINDOWS:
        monkeypatch.setattr(native_setup, "_write_registry", lambda value: written.update(registry=str(value)))
        monkeypatch.setattr(native_setup, "_delete_registry", lambda: written.update(deleted=True))
    return written


def frame(message):
    payload = json.dumps(message).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def frames_of(raw):
    """Parse a byte stream as native-messaging frames; leftover bytes prove corruption."""
    out = []
    while raw:
        if len(raw) < 4:
            raise AssertionError(f"stray bytes at the end of the channel: {raw!r}")
        (length,) = struct.unpack("<I", raw[:4])
        body, raw = raw[4 : 4 + length], raw[4 + length :]
        if len(body) < length:
            raise AssertionError("truncated frame")
        out.append(json.loads(body.decode("utf-8")))
    return out


# ── the wire itself ──────────────────────────────────────────────────────────


def test_the_host_writes_the_framing_mozilla_documents():
    out = io.BytesIO()
    host = firefox.NativeMessagingHost(stdin=io.BytesIO(b""), stdout=out)
    host.send({"type": "welcome", "ok": True})
    raw = out.getvalue()
    (length,) = struct.unpack("<I", raw[:4])
    assert length == len(raw) - 4
    assert json.loads(raw[4:].decode("utf-8")) == {"type": "welcome", "ok": True}


def test_the_host_reads_frames_one_by_one():
    stream = io.BytesIO(frame({"type": "hello"}) + frame({"type": "check"}))
    host = firefox.NativeMessagingHost(stdin=stream, stdout=io.BytesIO())
    assert host.read_message() == {"type": "hello"}
    assert host.read_message() == {"type": "check"}
    assert host.read_message() is None  # end of channel, not an error


def test_a_broken_or_oversized_frame_ends_the_channel_instead_of_crashing():
    host = firefox.NativeMessagingHost(stdin=io.BytesIO(struct.pack("<I", 50 * 1024 * 1024)), stdout=io.BytesIO())
    assert host.read_message() is None
    host = firefox.NativeMessagingHost(stdin=io.BytesIO(frame({"type": "hello"})[:5]), stdout=io.BytesIO())
    assert host.read_message() is None  # truncated frame


def test_the_channel_has_no_token_and_no_port():
    """Access control is the browser's manifest check, not a shared secret."""
    host = firefox.NativeMessagingHost(stdin=io.BytesIO(b""), stdout=io.BytesIO())
    assert host.token is None and host.port == 0 and host.connected is True


def test_a_pending_command_is_released_when_firefox_hangs_up():
    """A browser that vanishes mid-command must not leave the runner waiting."""
    read_fd, write_fd = os.pipe()
    host = firefox.NativeMessagingHost(stdin=os.fdopen(read_fd, "rb"), stdout=io.BytesIO())
    result = {}

    def attempt():
        try:
            result["command"] = host.command("observe", tabId=1)
        except Exception as error:  # the waiter must be told, not left hanging
            result["command"] = error

    threading.Thread(target=attempt, daemon=True).start()
    time.sleep(0.2)  # the command is on the wire, nobody will answer it
    os.close(write_fd)  # Firefox quits
    host.serve()  # returns at EOF and releases waiters
    deadline = time.time() + 5
    while not result and time.time() < deadline:
        time.sleep(0.05)
    assert isinstance(result.get("command"), firefox.BridgeError)
    assert "closed the connection" in str(result["command"])


def test_a_command_round_trips_over_the_channel():
    read_fd, write_fd = os.pipe()
    out = io.BytesIO()
    host = firefox.NativeMessagingHost(stdin=os.fdopen(read_fd, "rb"), stdout=out)
    threading.Thread(target=host.serve, daemon=True).start()
    result = {}
    def ask():
        result["answer"] = host.command("open", url="https://example.com")

    threading.Thread(target=ask, daemon=True).start()

    deadline = time.time() + 5
    sent = None
    while time.time() < deadline and sent is None:
        parsed = frames_of(out.getvalue())
        sent = parsed[0] if parsed else None
        time.sleep(0.05)
    assert sent is not None and sent["type"] == "open" and sent["url"] == "https://example.com"
    os.write(write_fd, frame({"id": sent["id"], "ok": True, "result": {"tabId": 7}}))

    deadline = time.time() + 5
    while not result and time.time() < deadline:
        time.sleep(0.05)
    assert result.get("answer") == {"tabId": 7}
    os.close(write_fd)


# ── the real thing: a browser launching the process ─────────────────────────


class FakeFirefox:
    """Spawns the host the way Firefox does and speaks the same frames."""

    def __init__(self, env):
        self.process = subprocess.Popen(
            [sys.executable, "-c", "from jev_ultrafast.firefox import native_main; native_main()"],
            cwd="/",  # the browser picks the working directory, not us
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.received = []
        self.errors = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_errors, daemon=True).start()

    def _pump(self):
        while True:
            header = self.process.stdout.read(4)
            if not header or len(header) < 4:
                return
            (length,) = struct.unpack("<I", header)
            body = self.process.stdout.read(length)
            if len(body) < length:
                return
            self.received.append(json.loads(body.decode("utf-8")))

    def _pump_errors(self):
        for line in self.process.stderr:
            self.errors.append(line.decode("utf-8", "replace").rstrip())

    def send(self, message):
        self.process.stdin.write(frame(message))
        self.process.stdin.flush()

    def until(self, predicate, seconds=15):
        deadline = time.time() + seconds
        while time.time() < deadline:
            found = next((item for item in self.received if predicate(item)), None)
            if found:
                return found
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()


@pytest.fixture
def browser(tmp_path, monkeypatch):
    for name in ("GROQ_API_KEY", "NVIDIA_API_KEY", "TYPESAFE_API_KEY", "POLICY_PROVIDER", "PLANNER_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.endswith("_API_KEY") and not key.startswith(("POLICY_", "PLANNER_", "TEXT_MODEL_"))
    }
    env["JEV_ENV_FILE"] = str(tmp_path / ".env")
    instance = FakeFirefox(env)
    yield instance
    instance.close()


def test_firefox_can_run_the_whole_first_run_setup_by_itself(browser, tmp_path):
    browser.send({"type": "hello"})
    welcome = browser.until(lambda m: m.get("type") == "welcome")
    assert welcome and welcome["ok"] is True
    assert welcome["state"]["setup"]["configured"] is False

    browser.send({"type": "setup.key", "variable": "GROQ_API_KEY", "value": "gsk_started_by_firefox"})
    assert browser.until(lambda m: m.get("type") == "notice" and "Saved" in m.get("message", ""))
    configured = browser.until(
        lambda m: m.get("type") == "state" and m["state"]["setup"]["configured"] is True
    )
    assert configured, [m.get("type") for m in browser.received]
    assert (tmp_path / ".env").read_text(encoding="utf-8").strip() == "GROQ_API_KEY=gsk_started_by_firefox"
    assert configured["state"]["setup"]["selection"]["planner"] == ["groq", "openai/gpt-oss-120b"]


def test_the_channel_carries_protocol_and_nothing_else(browser):
    """A stray print() would corrupt the stream: every byte must be a frame."""
    browser.send({"type": "hello"})
    assert browser.until(lambda m: m.get("type") == "welcome")
    browser.send({"type": "setup.status"})
    assert browser.until(lambda m: m.get("type") == "setup")
    browser.close()
    rest = browser.process.stdout.read()
    assert frames_of(rest) or rest == b""  # whatever is left still parses (or is empty)
    assert any("native messaging" in line for line in browser.errors), "diagnostics go to stderr"


def test_the_host_survives_a_message_it_does_not_understand(browser):
    browser.send({"type": "hello"})
    assert browser.until(lambda m: m.get("type") == "welcome")
    browser.send({"type": "nonsense"})
    assert browser.until(lambda m: m.get("type") == "error" and "nonsense" in m.get("message", ""))
    browser.send({"type": "setup.status"})  # and it is still listening
    assert browser.until(lambda m: m.get("type") == "setup")


def test_the_host_stops_when_firefox_closes_the_channel(browser):
    browser.send({"type": "hello"})
    assert browser.until(lambda m: m.get("type") == "welcome")
    browser.process.stdin.close()
    assert browser.process.wait(timeout=8) is not None
    assert browser.process.poll() is not None


def test_the_host_finds_its_own_folder_whatever_the_browser_cwd_is(browser, tmp_path):
    """Firefox does not launch hosts from the project folder; .env and workspace/ must still resolve."""
    browser.send({"type": "setup.key", "variable": "GROQ_API_KEY", "value": "gsk_anywhere"})
    assert browser.until(lambda m: m.get("type") == "notice")
    # JEV_ENV_FILE points at tmp_path, but the default .env resolution must not be the browser's cwd
    assert firefox.repo_root().name == ROOT.name
    assert (firefox.repo_root() / "pyproject.toml").exists()


# ── registration: the one thing the user does (once) ────────────────────────


def test_registration_writes_the_exact_manifest_firefox_looks_for(tmp_path, monkeypatch):
    written = isolate_user(monkeypatch, tmp_path)
    entry = native_setup.register(root=ROOT, path=tmp_path / "jev-firefox-native")
    assert entry["manifest"].name == f"{native_setup.HOST_NAME}.json"
    assert entry["manifest"].parent.name in {"native-messaging-hosts", "NativeMessagingHosts"}
    manifest = json.loads(Path(entry["manifest"]).read_text(encoding="utf-8"))
    assert manifest["name"] == native_setup.HOST_NAME
    assert manifest["type"] == "stdio"
    assert manifest["allowed_extensions"] == [native_setup.EXTENSION_ID]
    assert Path(manifest["path"]).is_absolute()  # macOS/Linux require absolute
    assert re.fullmatch(r"\w+(\.\w+)*", manifest["name"]), "Firefox rejects other names"
    assert native_setup.status()["registered"] is True
    if WINDOWS:  # the browser finds it through HKCU, not through a known folder
        assert written["registry"] == str(entry["manifest"])
    assert native_setup.unregister() is True
    if WINDOWS:
        assert written.get("deleted") is True
    assert native_setup.status()["registered"] is False


def test_registration_points_at_a_program_that_exists(tmp_path, monkeypatch):
    """Whatever the install shape (starter venv, pip, uv), the browser must find a real program."""
    isolate_user(monkeypatch, tmp_path)
    entry = native_setup.register(root=ROOT)
    assert entry["exists"] is True, f"{entry['executable']} does not exist"
    assert Path(str(entry["executable"])).name.startswith(native_setup.BUILD_SCRIPT)


def test_the_checkout_venv_wins_over_the_path_when_both_exist(tmp_path, monkeypatch):
    isolate_user(monkeypatch, tmp_path)
    checkout = tmp_path / "checkout"
    scripts, suffix = ("Scripts", ".exe") if os.name == "nt" else ("bin", "")
    entry = checkout / ".venv" / scripts / f"{native_setup.BUILD_SCRIPT}{suffix}"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    assert native_setup.executable(checkout) == entry


def test_status_is_honest_when_nothing_is_registered(tmp_path, monkeypatch):
    isolate_user(monkeypatch, tmp_path)
    state = native_setup.status()
    assert state == {
        "manifest": native_setup.manifest_path(),
        "registered": False,
        "executable": None,
        "exists": False,
    }


# ── the three files that must agree, or nothing works ───────────────────────


def test_the_extension_asks_for_the_host_the_installer_registered():
    background = (ROOT / "extension/background.js").read_text(encoding="utf-8")
    name = re.search(r'const NATIVE_HOST = "([^"]+)"', background).group(1)
    assert name == native_setup.HOST_NAME


def test_the_manifest_allows_exactly_our_add_on():
    manifest = json.loads((ROOT / "extension/manifest.json").read_text(encoding="utf-8"))
    assert manifest["permissions"].count("nativeMessaging") == 1
    assert manifest["browser_specific_settings"]["gecko"]["id"] == native_setup.EXTENSION_ID


def test_the_extension_still_works_when_native_messaging_is_not_registered():
    background = (ROOT / "extension/background.js").read_text(encoding="utf-8")
    assert "nativeUnavailable" in background and "openSocket()" in background
    assert re.search(r"if \(!nativeUnavailable && \(await tryNative\(\)\)\) return;", background)


def test_a_double_click_leaves_the_host_registered_with_firefox():
    """The guarantee is the same; since 0.12.6 it lives in the installer the starters call.

    The starters no longer run jev-register-host themselves - that made every double-click register
    again, and a machine that was already registered could not tell. `scripts/install.py` decides
    whether Firefox needs telling (`register` step) and reports the outcome.
    """
    installer = (ROOT / "scripts" / "install.py").read_text(encoding="utf-8")
    assert "jev-register-host" in installer, "nothing registers the host any more"
    for name in ("start-host.sh", "start-host.command", "start-host.bat"):
        text = (ROOT / name).read_text(encoding="utf-8").replace("\\", "/")
        assert "scripts/install.py" in text, f"{name} never reaches the installer"


def test_the_console_scripts_are_declared():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'jev-firefox-native = "jev_ultrafast.firefox:native_main"' in pyproject
    assert 'jev-register-host = "jev_ultrafast.native_setup:main"' in pyproject
