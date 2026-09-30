"""Firefox extension bridge: a stdlib WebSocket server, a Browser-compatible tab driver, and a task runner.

The extension (see extension/) connects to ws://127.0.0.1:8767 and exposes one live tab.
The host sends observe/act/fresh/open commands; the extension replies with results.
Task runs arrive as "run" messages and drive the normal Agent loop with FirefoxBrowser.
"""

import base64
import hashlib
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time
from pathlib import Path

from . import neko, permissions
from .browser import StalePage, fingerprint
from .model import policy_description
from .neko import SandboxBrowser
from .parameters import (
    PARAMETER_SCHEMA,
    PRESETS,
    apply_model,
    apply_params,
    apply_preset,
    apply_profile,
    apply_saved_config,
    current_selection,
    delete_profile,
    load_config,
    model_for,
    profile_names,
    prune_params,
    save_config,
    save_profile,
)
from .redact import redact


def check_providers(on_event=None):
    """One tiny real request per configured role; per-role status for the sidebar.

    This is the setup self-test: it shows exactly which model works and, when
    one fails, the provider's own error (wrong key, wrong model id, ...).

    On a free tier this is not instant: an endpoint that has to wake up can take
    tens of seconds per role, and the whole check runs them one after another.
    `on_event(kind, payload)` therefore reports each role as it starts and finishes
    ("start"/"done") so the sidebar can show the wait instead of a frozen panel.
    """
    from . import providers as provider_layer

    def announce(kind, payload):
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:  # noqa: BLE001 - progress is a courtesy; it never breaks the check
                pass

    roles = []
    if provider_layer.planner_enabled():
        roles.append("planner")
    roles.append("policy")
    roles.append("text")
    results = {}
    for role in roles:
        entry = {"role": role, "model": None, "ok": False, "latency_ms": None, "detail": ""}
        provider = None
        try:
            if role == "policy" and os.environ.get("TYPESAFE_API_KEY"):
                entry.update(model="jev-latest (TypeSafe)", ok=True, detail="TypeSafe key configured", latency_ms=0)
            else:
                provider = provider_layer.resolve(role)
                entry["model"] = f"{provider['name']}:{provider['model']}"
                announce("start", {"role": role, "model": entry["model"]})
                started = time.perf_counter()
                provider_layer.chat(
                    provider,
                    "You are a connectivity check. Reply with exactly the JSON object {} and nothing else.",
                    "ping",
                    max_tokens=64,
                )
                entry.update(ok=True, latency_ms=round((time.perf_counter() - started) * 1000), detail="connected")
        except ValueError as error:
            entry["detail"] = str(error)
            if role == "text" and "API_KEY" in str(error):
                entry["detail"] += " (only needed when the agent types into fields)"
        except RuntimeError as error:
            entry["detail"] = str(error)[:300]
            if "404" in entry["detail"] or "not found" in entry["detail"].lower():
                entry["detail"] += " — check the exact model id in the provider's catalogue"
            elif "401" in entry["detail"] or "403" in entry["detail"]:
                keys_url = (provider or {}).get("keys_url") if provider else None
                preset = provider_layer.PROVIDERS.get((provider or {}).get("name", ""), {})
                keys_url = keys_url or preset.get("keys_url")
                where = f" at {keys_url}" if keys_url else ""
                entry["detail"] += (
                    f" — this is an API-key problem: the provider rejected the key. Generate a fresh key{where},"
                    " paste it in .env without quotes or extra characters, save, and restart the starter."
                )
        results[role] = entry
        announce("done", dict(entry))
    return results


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_PORT = 8767
COMMAND_TIMEOUT = 60.0
NATIVE_MESSAGE_LIMIT = 1024 * 1024  # Firefox rejects anything larger from the app


RUNS_LOG = Path(os.environ.get("JEV_RUNS_LOG", "artifacts/runs.jsonl"))
DELTA_BROADCAST_INTERVAL = 0.15  # seconds between live "thinking" broadcasts


class BridgeError(RuntimeError):
    """The extension is unreachable or rejected a command."""


class ApprovalGate:
    """Asks the sidebar to approve a sensitive tool action; denies on timeout."""

    def __init__(self, bridge, timeout=120.0):
        self.bridge = bridge
        self.timeout = timeout
        self._pending = {}

    def request(self, command):
        approval_id = secrets.token_hex(8)
        entry = {"event": threading.Event(), "approved": False}
        self._pending[approval_id] = entry
        try:
            self.bridge.broadcast({"type": "approval_request", "id": approval_id, "command": command})
        except Exception:  # noqa: BLE001 - no sidebar connected: fail closed
            self._pending.pop(approval_id, None)
            return False
        answered = entry["event"].wait(self.timeout)
        self._pending.pop(approval_id, None)
        return bool(answered and entry["approved"])

    def respond(self, approval_id, approved):
        entry = self._pending.get(approval_id)
        if entry is None:
            return False
        entry["approved"] = bool(approved)
        entry["event"].set()
        return True


def _read_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data


def _read_frame(sock):
    head = _read_exact(sock, 2)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(sock, 8))[0]
    mask = _read_exact(sock, 4) if masked else None
    payload = _read_exact(sock, length)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def _send_frame(sock, payload, opcode=1):
    head = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        head += bytes([length])
    elif length < 65536:
        head += bytes([126]) + struct.pack(">H", length)
    else:
        head += bytes([127]) + struct.pack(">Q", length)
    sock.sendall(head + payload)


class Bridge:
    """Transport-independent half of the bridge: routing, request ids, and the task runner.

    Two transports speak it, and the sidebar cannot tell them apart:
      * ``BridgeServer``     - the loopback WebSocket, used when someone double-clicks the host.
      * ``NativeMessagingHost`` - Firefox starts the host itself and talks over stdin/stdout,
        so there is no port, no window and no token to configure.
    """

    def __init__(self, token=None):
        self.token = token
        self.runner = None
        self._send_lock = threading.Lock()
        self._pending = {}
        self._next_id = 1

    @property
    def connected(self):
        raise NotImplementedError

    def _write_locked(self, message):
        """Put one message on the wire; the caller already holds ``_send_lock``."""
        raise NotImplementedError

    def close(self):
        pass

    def _fail_pending(self, reason):
        """Nobody will answer these: unblock every waiter with the reason."""
        for entry in self._pending.values():
            entry["error"] = reason
            entry["event"].set()
        self._pending = {}

    def _route(self, message):
        if "id" in message and ("ok" in message or "error" in message):
            entry = self._pending.pop(message["id"], None)
            if entry:
                entry["result" if message.get("ok") else "error"] = message.get("result") or message.get("error")
                entry["event"].set()
            return
        kind = message.get("type")
        if kind == "hello":
            if self.token and not secrets.compare_digest(message.get("token", ""), self.token):
                self.send({"type": "welcome", "ok": False, "error": "Invalid bridge token"})
                return
            self.send({"type": "welcome", "ok": True, "state": self.runner.current_state() if self.runner else None})
            if self.runner:
                self.runner.greet()  # the welcome is on the wire before anything else is
                threading.Thread(target=self.runner.run_provider_check, daemon=True).start()
        elif kind == "setup.key":
            if self.runner:
                # the sidebar's first-run form: never blocks the reader thread
                threading.Thread(
                    target=self.runner.handle_setup_key,
                    args=(message.get("variable"), message.get("value")),
                    daemon=True,
                ).start()
        elif kind == "setup.status":
            from . import providers as provider_layer

            self.send({"type": "setup", **provider_layer.setup_status()})
        elif kind == "check":
            if self.runner:
                self.send({"type": "checking"})
                threading.Thread(target=self.runner.run_provider_check, daemon=True).start()
        elif kind == "run":
            if self.runner is None:
                self.send({"type": "error", "message": "Task runner is not active"})
                return
            try:
                self.runner.start(message.get("goal", ""), message.get("url", ""), message.get("tabId"))
            except (ValueError, RuntimeError) as error:
                self.send({"type": "error", "message": str(error)})
        elif kind == "stop":
            if self.runner:
                self.runner.stop_task()
        elif kind == "state":
            self.send({"type": "state", "state": self.runner.current_state() if self.runner else None})
        elif kind == "models":
            if self.runner:
                self.runner.handle_models(message.get("refresh"))
        elif kind == "models.select":
            if self.runner:
                self.runner.handle_model_select(message)
        elif kind == "models.probe":
            if self.runner:
                self.runner.handle_model_probe(message)
        elif kind == "params.set":
            if self.runner:
                self.runner.handle_params_set(message)
        elif kind == "approval_response":
            if self.runner and self.runner.approvals:
                self.runner.approvals.respond(message.get("id", ""), message.get("approved"))
        elif kind in {"profile.save", "profile.apply", "profile.delete"}:
            if self.runner:
                self.runner.handle_profile(kind.split(".")[1], message.get("name", ""))
        elif kind == "mode.set":
            if self.runner:
                self.runner.handle_mode(message.get("mode"))
        elif kind == "permissions.get":
            if self.runner:
                self.runner.handle_permissions()
        elif kind == "permissions.set":
            if self.runner:
                self.runner.handle_permissions(scope=message.get("scope"), level=message.get("level"))
        elif kind == "permissions.reset":
            if self.runner:
                self.runner.handle_permissions(reset=True)
        elif kind in {"sandbox.start", "sandbox.stop", "sandbox.open"}:
            if self.runner:
                # bring-up can take ~45 s (docker run + readiness): never block the reader
                threading.Thread(
                    target=self.runner.handle_sandbox, args=(kind.split(".", 1)[1],), daemon=True
                ).start()
        else:
            self.send({"type": "error", "message": f"Unknown message type: {kind}"})

    def send(self, message):
        with self._send_lock:
            self._write_locked(message)

    def broadcast(self, message):
        try:
            self.send(message)
        except (BridgeError, OSError):
            pass  # broadcasts are best-effort; the sidebar refreshes on reconnect

    def command(self, kind, **payload):
        with self._send_lock:
            if not self.connected:
                raise BridgeError(
                    "Firefox extension is not connected. Start the host (jev-firefox), "
                    "install the extension via about:debugging, and open the sidebar."
                )
            command_id = self._next_id
            self._next_id += 1
            entry = {"event": threading.Event()}
            self._pending[command_id] = entry
            self._write_locked({"id": command_id, "type": kind, **payload})
        if not entry["event"].wait(COMMAND_TIMEOUT):
            self._pending.pop(command_id, None)
            raise BridgeError(f"Firefox extension did not answer '{kind}' within {COMMAND_TIMEOUT:.0f}s")
        if "error" in entry:
            raise BridgeError(str(entry["error"]))
        return entry.get("result")


class BridgeServer(Bridge):
    """A single-client WebSocket server bound to loopback; the Firefox extension is the client."""

    def __init__(self, port=0, token=None):
        super().__init__(token=token)
        self._client = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]

    @property
    def connected(self):
        return self._client is not None

    def _write_locked(self, message):
        client = self._client
        if client is None:
            raise BridgeError("Firefox extension is not connected")
        _send_frame(client, json.dumps(message).encode("utf-8"))

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def start(self):
        thread = threading.Thread(target=self._accept_loop, daemon=True)
        thread.start()
        return thread

    def _accept_loop(self):
        while True:
            try:
                conn, _address = self._sock.accept()
            except OSError:
                return
            try:
                self._serve(conn)
            except (ConnectionError, OSError, ValueError):
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                self._clear_client(conn)

    def _clear_client(self, conn):
        with self._send_lock:
            if self._client is conn:
                self._client = None
        self._fail_pending("Firefox extension disconnected")

    def _serve(self, conn):
        headers = self._read_handshake(conn)
        origin = headers.get("origin", "")
        # Firefox always sends Origin on a WebSocket handshake; no local process does. Accepting
        # its absence let any process on the machine drive the agent (H3, verified with raw sockets:
        # a handshake with no Origin went from 101 to 403).
        if not origin or not origin.startswith("moz-extension://"):
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            raise ValueError("Forbidden origin")
        accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
        conn.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        with self._send_lock:
            self._client = conn
        while True:
            opcode, payload = _read_frame(conn)
            if opcode == 8:
                return
            if opcode == 9:
                with self._send_lock:
                    _send_frame(conn, payload, opcode=10)
                continue
            if opcode != 1:
                continue
            try:
                self._route(json.loads(payload.decode("utf-8")))
            except Exception as error:  # a malformed message must not kill the reader
                self.broadcast({"type": "error", "message": f"Bridge error: {error}"})

    @staticmethod
    def _read_handshake(conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                raise ConnectionError("Closed during handshake")
            data += chunk
            if len(data) > 16384:
                raise ValueError("Oversized handshake")
        headers = {}
        for line in data.decode("latin-1").split("\r\n\r\n", 1)[0].split("\r\n")[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        if "sec-websocket-key" not in headers:
            raise ValueError("Not a WebSocket handshake")
        return headers



class NativeMessagingHost(Bridge):
    """Firefox starts this process itself and talks over stdin/stdout.

    This is the setup-free transport: no port to free, no window to keep open, no
    token to configure - the browser only runs a host whose manifest lists this
    add-on id, which is the access control. Frame format is the documented one:
    a 4-byte-length header in native byte order followed by UTF-8 JSON.
    """

    port = 0  # nothing listens; kept so callers that print a port keep working

    def __init__(self, stdin=None, stdout=None):
        super().__init__(token=None)
        self._in = stdin if stdin is not None else sys.stdin.buffer
        self._out = stdout if stdout is not None else sys.stdout.buffer
        self._closed = False

    @property
    def connected(self):
        return not self._closed

    def _write_locked(self, message):
        frame = json.dumps(message, separators=(",", ":")).encode("utf-8")
        self._out.write(struct.pack("<I", len(frame)) + frame)
        self._out.flush()

    def close(self):
        self._closed = True

    def read_message(self, limit=NATIVE_MESSAGE_LIMIT):
        """One message from Firefox; None means the browser closed the channel."""
        header = self._in.read(4)
        if not header or len(header) < 4:
            return None
        length = struct.unpack("<I", header)[0]
        if length == 0 or length > limit:
            return None
        payload = self._in.read(length)
        if not payload or len(payload) < length:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except ValueError:
            return None

    def serve(self):
        """Read until Firefox goes away (stanza by stanza, like the WebSocket reader)."""
        while True:
            try:
                message = self.read_message()
            except (ValueError, OSError):
                break
            if message is None:
                break
            try:
                self._route(message)
            except Exception as error:  # a malformed message must not kill the host
                self.broadcast({"type": "error", "message": f"Bridge error: {error}"})
        self._closed = True
        self._fail_pending("Firefox closed the connection")


def repo_root():
    """The folder this checkout lives in, wherever the browser started us from."""
    from . import providers as provider_layer

    return provider_layer.repo_root() or Path.cwd()


def native_main():
    """Entry point Firefox launches by itself: no window, no port, nothing to click."""
    real_stdout = os.dup(1)
    os.dup2(2, 1)  # anything printed from here on goes to stderr; stdout is the protocol
    os.chdir(repo_root())  # .env, workspace/ and artifacts/ live next to the code
    load_environment()
    host = NativeMessagingHost(stdout=os.fdopen(real_stdout, "wb", buffering=0))
    host.runner = TaskRunner(host)
    from . import providers as provider_layer

    state = "configured" if provider_layer.is_configured() else "no API key yet - paste one in the sidebar"
    print(f"Agent host started by Firefox (native messaging): {state}.", file=sys.stderr, flush=True)
    try:
        host.serve()
    except KeyboardInterrupt:
        pass


class FirefoxBrowser:
    """Drives one live Firefox tab through the extension bridge; the same contract as Browser."""

    def __init__(self, url, tab_id=None, bridge=None):
        self.bridge = bridge if bridge is not None else server()
        self.tab_id = tab_id
        if tab_id is None:
            self.tab_id = self.bridge.command("open", url=url)["tabId"]

    def observe(self, screenshot=True):
        state = None
        for attempt in range(10):
            try:
                state = self.bridge.command("observe", tabId=self.tab_id, screenshot=screenshot)
                break
            except BridgeError as error:
                transient = "navigating" in str(error) or "receiving end" in str(error).lower()
                if attempt == 9 or not transient:
                    raise
                time.sleep(0.05 + 0.05 * attempt)
        state["fingerprint"] = fingerprint(state)
        return state

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.bridge.command("fresh", tabId=self.tab_id, node=node)
            return current == [page["page_key"], page["guards"].get(str(node))]
        current = self.bridge.command("fresh", tabId=self.tab_id, marker=page["marker"])
        return current == page["marker"]

    def act(self, action, page, text=None):
        try:
            if not self.fresh(page, action):
                raise StalePage("Page changed since this decision. Observe again.")
            if action["kind"] == "wait":
                time.sleep(0.1)
                return {"executed": action["id"]}
            result = self.bridge.command("act", tabId=self.tab_id, action=action, text=text)
        except BridgeError as error:
            # A vanished content-script context means the tab navigated mid-action:
            # treat it as a stale page so the agent re-observes instead of dying.
            if "receiving end" in str(error).lower() or "context" in str(error).lower():
                raise StalePage("The tab changed while acting. Observe again.") from None
            raise
        if result.get("stale"):
            raise StalePage("Target changed or is covered. Observe again.")
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result

    def close(self):
        pass  # the tab belongs to the user's browser; the bridge leaves it open


_SERVER = None


def server():
    if _SERVER is None or not _SERVER.connected:
        raise BridgeError(
            "Firefox extension is not connected. Run `uv run --env-file .env jev-firefox`, "
            "load extension/ via about:debugging, and open the sidebar."
        )
    return _SERVER


class TaskRunner:
    """Runs one task at a time on the extension's tab and broadcasts each state.

    Two routes per the product spec: plain browser missions go straight to the
    fast JEV loop (Agent), while missions that need search, files, documents,
    or the terminal run through the orchestrator — which can still delegate
    browser steps back to the Agent as the `browser_task` tool.
    """

    def __init__(self, bridge, workspace=None):
        self.bridge = bridge
        self.agent = None
        self.stopped = False
        self.last_error = None
        self.provider_check = None
        self.checking = None  # epoch seconds while a self-test is in flight, else None
        self.approvals = None
        self.mode = "browser"
        self.orchestrated = None
        self._trace = None  # per-task id tying runs.jsonl, the audit log, and broadcasts
        self._audited_steps = 0
        self.mode_pref = (os.environ.get("JEV_BROWSER_MODE") or "live").strip().lower()
        if self.mode_pref not in {"live", "sandbox"}:
            self.mode_pref = "live"
        self.neko = neko.NekoSessionManager(on_event=self._audit_event)
        self.sandbox_session = None
        self.docker_available = None  # probed in the background; None means "checking"
        self._typesafe_key = os.environ.get("TYPESAFE_API_KEY")  # kept so the UI can switch back
        self._workspace = Path(workspace) if workspace else Path.cwd() / "workspace"
        self._lock = threading.Lock()  # held by a running task
        self._check_lock = threading.Lock()  # serializes provider self-tests
        self._config_epoch = 0  # bumped when a key arrives: invalidates in-flight checks
        try:
            apply_saved_config()
        except Exception:  # noqa: BLE001 - a broken config file must never block startup
            pass
        from . import laya_install, laya_local

        # Laya is installed by default: it answers the routing and skill decisions in any
        # language, locally (195 ms per decision measured on a 2-core CPU). Nothing blocks
        # on it — the install runs in the background and reports what it is doing in the panel.
        laya_install.ensure_async(on_event=self._audit_event, on_done=self._laya_done)
        laya_local.warm()  # already-installed case: preload the weights right away

    def _laya_done(self, ok, detail):
        """The install finished (or declined to run): warm it and refresh the panel.

        Never overtakes the welcome message — the sidebar connects after the host starts.
        """
        if ok:
            from . import laya_local

            laya_local.warm()
        if getattr(self, "_greeted", False):
            self._broadcast()

    def greet(self):
        """A sidebar connected and received its welcome; background probes may start."""
        self._greeted = True
        self._ensure_sandbox_probe()

    def _ensure_sandbox_probe(self):
        if getattr(self, "_probe_started", False):
            return
        self._probe_started = True
        threading.Thread(target=self._probe_sandbox, daemon=True).start()

    def _probe_sandbox(self):
        """Is Docker present? The answer only affects the isolated-browser toggle."""
        try:
            self.docker_available = bool(self.neko.available())
        except Exception:  # noqa: BLE001 - a broken docker must never block the host
            self.docker_available = False
        self.sandbox_session = self.neko.current()
        if getattr(self, "_greeted", False):
            self._broadcast()  # never overtake the welcome message

    def sandbox_state(self):
        self._ensure_sandbox_probe()
        session = self.sandbox_session or self.neko.current()
        self.sandbox_session = session
        if self.docker_available is True:
            reason = None
        elif self.docker_available is False:
            reason = "Docker was not found on this machine; install it to use the isolated browser."
        else:
            reason = "Checking whether Docker is available…"
        return {"available": self.docker_available, "reason": reason, "session": session}

    def current_state(self):
        from . import laya_install
        from . import providers as provider_layer
        from .orchestrator import MAX_ORCHESTRATOR_STEPS

        selection = current_selection()
        typesafe = bool(self._typesafe_key or os.environ.get("TYPESAFE_API_KEY"))
        common = {
            "mode": self.mode,
            "trace": self._trace,
            "policy": policy_description(),
            "error": self.last_error,
            "providers": self.provider_check,
            # A self-test can take minutes on a free tier: the sidebar shows the wait instead
            # of a verdict from before it.
            "checking": self.checking,
            "selection": selection,
            "schema": PARAMETER_SCHEMA,
            "presets": PRESETS,
            "policy_builtin": typesafe,
            "profiles": profile_names(),
            "tokens": self._tokens(),
            "permissions": permissions.describe(),
            "browserMode": self.mode_pref,
            # How many steps an orchestrated run may take at most. The sidebar shows it
            # next to the live step number: a cap is honest, an invented total is not.
            "step_budget": MAX_ORCHESTRATOR_STEPS,
            "sandbox": self.sandbox_state(),
            "setup": provider_layer.setup_status(),
            # The local decision engine, with the same words the log uses. Default-on: it is
            # the difference between a couple of local decisions and a cloud call per step.
            "laya": laya_install.state(),
        }
        if self.mode == "orchestrated":
            # The orchestrated view; the live browser sub-view rides under "browser".
            current = {"status": "idle", "history": [], "plan": [], "page": None, **common}
            current.update(self.orchestrated or {})
            if self.agent is not None:
                snap = self.agent.snapshot()
                current["browser"] = snap
                current["planner"] = snap.get("planner")
            return current
        if self.agent is None:
            state = {"status": "idle", "history": [], "plan": [], "page": None}
        else:
            state = self.agent.snapshot()
        return {**state, **common, "planner": self.agent.state.get("planner") if self.agent else None}

    def _tokens(self):
        """Total tokens used by the current/last run, for the sidebar footer."""
        total = 0
        entries = []
        if self.agent is not None:
            state = self.agent.state
            entries = list(state.get("decisions") or []) + list(state.get("text_calls") or [])
        elif self.orchestrated:
            entries = [{"usage": self.orchestrated.get("usage") or {}}]
        for entry in entries:
            usage = entry.get("usage") or {}
            for field in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"):
                value = usage.get(field)
                if isinstance(value, (int, float)):
                    total += value
        return total

    def handle_setup_key(self, variable, value):
        """A key pasted in the sidebar: save it to .env, then re-test in the background."""
        from . import providers as provider_layer

        try:
            name = provider_layer.save_key(variable, value)
        except ValueError as error:
            self.bridge.broadcast({"type": "error", "message": str(error)})
            return
        # only the variable name travels back - never the key
        self._config_epoch += 1  # any check already in flight was started without this key
        self.bridge.broadcast({"type": "notice", "message": f"Saved {name} to .env. Testing it now…"})
        self._broadcast()  # the checklist ticks before the network round trip
        threading.Thread(target=self._recheck_after_setup, daemon=True).start()

    def _recheck_after_setup(self):
        """A key just arrived: publish a report that reflects it, not one that raced it."""
        deadline = time.time() + 30
        while time.time() < deadline:
            if self._check_lock.acquire(blocking=False):
                break
            time.sleep(0.2)  # a check started before the key is still running; wait it out
        else:
            return  # it is stuck; the next "Test setup" press will report instead
        epoch = self._config_epoch
        self._checking_started()
        try:
            report = check_providers(self._check_progress)
        finally:
            self._check_lock.release()
            self._checking_finished()
        if epoch == self._config_epoch:
            self.provider_check = report
            self._broadcast()

    def run_provider_check(self):
        """Self-test in the background; the result is carried in every state broadcast.

        Uses its own lock: a check never blocks (and never gets confused with) a task,
        so pressing Run right after connecting always works.
        """
        if self._lock.locked():
            return  # a task is running; providers are clearly working
        if not self._check_lock.acquire(blocking=False):
            return  # another check is already in flight
        epoch = self._config_epoch
        self._checking_started()
        try:
            report = check_providers(self._check_progress)
        finally:
            self._check_lock.release()
            self._checking_finished()
        if epoch != self._config_epoch:
            return  # a key was saved while this ran: its verdict is already obsolete
        self.provider_check = report
        self._broadcast()

    def _checking_started(self):
        """Tell the sidebar a self-test is in flight, so it shows the wait and not the old verdict."""
        self.checking = time.time()
        self._broadcast()

    def _checking_finished(self):
        self.checking = None
        self._broadcast()

    def _check_progress(self, kind, payload):
        """One line per step of the self-test: which role is being asked, and what came back.

        A free endpoint that has to wake up takes tens of seconds, and the whole check runs
        the roles one after another; without this the sidebar only had a frozen panel to
        offer for minutes. The lines carry the role, the model, and the measured latency —
        never a key.
        """
        from . import providers as provider_layer

        label = provider_layer.ROLE_LABELS.get(payload.get("role"), payload.get("role"))
        if kind == "start":
            self.bridge.broadcast({"type": "notice", "message": f"Testing {label} ({payload.get('model')})…"})
            return
        if payload.get("ok"):
            self.bridge.broadcast(
                {"type": "notice", "message": f"{label} answered in {payload.get('latency_ms')} ms"}
            )
            return
        detail = (payload.get("detail") or "").strip() or "no answer"
        self.bridge.broadcast({"type": "notice", "message": f"{label} FAILED — {detail}"})

    def start(self, goal, url, tab_id):
        goal = (goal or "").strip()
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        if not self._lock.acquire(blocking=False):
            raise ValueError("A task is already running; stop it first")
        self.stopped = False
        self.last_error = None
        self.approvals = None
        self._trace = secrets.token_hex(6)
        from .orchestrator import route_task

        self.mode = route_task(goal)
        where = "isolated" if self.mode_pref == "sandbox" else "live"
        self._audit_event(
            "mission", json.dumps({"goal": goal[:200], "target": self.mode_pref}),
            ok=True,
            preview=f"{self.mode} mission on the {where} browser",
        )
        if self.mode == "orchestrated":
            self.orchestrated = {"status": "running", "log": [], "final": None, "skills": []}
            threading.Thread(target=self._run_orchestrated, args=(goal, url, tab_id), daemon=True).start()
        else:
            self.orchestrated = None
            threading.Thread(target=self._run, args=(goal, url, tab_id), daemon=True).start()

    def stop_task(self):
        self.stopped = True

    # ── execution target: the live tab, or the isolated Neko browser (MVP-5) ──

    def _browser(self, url, tab_id=None):
        if self.mode_pref != "sandbox":
            return FirefoxBrowser(url, tab_id=tab_id, bridge=self.bridge)
        session = self.sandbox_session or self.neko.current()
        if session is None or session.get("state") != "running":
            session = self.neko.start()  # raises NekoUnavailable with the actionable reason
            self.sandbox_session = session
        else:
            self.sandbox_session = session
        return SandboxBrowser(url, cdp_url=session["cdpUrl"])

    # ── sidebar permission center + sandbox handlers (MVP-5) ──────────────────

    def handle_mode(self, mode):
        mode = (mode or "").strip().lower()
        if mode not in {"live", "sandbox"}:
            self.bridge.send({"type": "error", "message": f"Unknown browser mode: {mode}"})
            return
        self.mode_pref = mode
        self._audit_event("browser_target", json.dumps({"mode": mode}), ok=True, preview=f"target → {mode}")
        self._broadcast()

    def handle_permissions(self, scope=None, level=None, reset=False):
        try:
            if reset:
                permissions.reset()
            elif scope:
                permissions.set_level(scope, level)
            # permissions.set_level audits through the center only inside a task;
            # here the sidebar is the actor, so record it explicitly.
            if scope or reset:
                self._audit_event(
                    "permissions", json.dumps({"scope": scope, "level": level, "reset": bool(reset)}),
                    ok=True, preview="permission change from the sidebar",
                )
        except ValueError as error:
            self.bridge.send({"type": "error", "message": redact(f"Permission error: {error}")})
            return
        self._broadcast()

    def _sandbox_gate(self):
        """The browser scope governs starting an isolated session (spec §10/§11)."""
        current = permissions.level("browser")
        if current == "deny":
            raise neko.NekoUnavailable(
                "The browser scope is set to deny, so no browser session can start. "
                "Change it in the permission center."
            )
        if current == "ask" and not ApprovalGate(self.bridge).request("Open the isolated Neko browser?"):
            raise neko.NekoUnavailable("The isolated browser was not approved in the sidebar.")

    def handle_sandbox(self, action):
        try:
            if action == "start":
                self._sandbox_gate()
                session = self.neko.start()
                self.sandbox_session = session
                if session.get("state") != "running":
                    self.bridge.broadcast({
                        "type": "error",
                        "message": (
                            "The isolated browser is running, but its CDP port did not answer, so the agent cannot "
                            "drive it. Use a Neko image that exposes remote debugging (NEKO_BROWSER_ARGS) and check "
                            "NEKO_CDP_PORT."
                        ),
                    })
            elif action == "stop":
                session = self.sandbox_session or self.neko.current()
                if session:
                    self.neko.stop(session["id"])
                self.sandbox_session = None
            elif action == "open":
                session = self.sandbox_session or self.neko.current()
                if session:
                    self.bridge.command("open", url=session["webUrl"])
        except neko.NekoUnavailable as error:
            self.bridge.broadcast({"type": "error", "message": redact(str(error))})
        self._broadcast()

    def _audit_event(self, tool, args=None, **extra):
        """One audit line from outside the ToolBox (missions, targets, permissions, sandbox)."""
        try:
            from .tools import AUDIT_LOG

            AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.time(),
                "trace": self._trace,
                "tool": tool,
                "args": redact(str(args or ""))[:400],
                **extra,
            }
            with AUDIT_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # the audit log must never break a run

    def _audit_steps(self, snapshot):
        """Every executed browser action is audited; typed text is redacted (spec §11)."""
        history = (snapshot or {}).get("history") or []
        if len(history) <= self._audited_steps:
            return
        for item in history[self._audited_steps:]:
            self._audit_event(
                "browser_action",
                json.dumps({"action": item.get("action"), "text": item.get("text")}),
                ok=True,
                duration_ms=item.get("latency_ms"),
                preview=redact(str(item.get("action") or ""))[:120],
            )
        self._audited_steps = len(history)

    # ── browser mode (the original fast path) ─────────────────────────────────

    def _run(self, goal, url, tab_id):
        from .agent import Agent  # imported here to keep the module import-light

        try:
            browser = self._browser(url, tab_id)
            self.agent = Agent(url, goal, screenshots=True, browser=browser)
            self._audited_steps = 0
            self._broadcast()
            for _state in self.agent.run():
                self._audit_steps(_state if isinstance(_state, dict) else self.agent.snapshot())
                self._broadcast()
                if self.stopped:
                    break
        except (ValueError, RuntimeError, BridgeError, StalePage) as error:
            # Keep the failure in the state so the sidebar shows it until the next run.
            self.last_error = redact(str(error))
            self.bridge.broadcast({"type": "error", "message": redact(str(error))})
        finally:
            snap = self.agent.snapshot() if self.agent is not None else {}
            self._record_run(
                "browser", goal,
                status="stopped" if self.stopped else (snap.get("status") or "done"),
                steps=len(snap.get("history") or []),
                elapsed_ms=snap.get("elapsed_ms") or 0,
            )
            self.agent = None
            self._broadcast()
            self._lock.release()

    # ── orchestrated mode (MVP-3) ─────────────────────────────────────────────

    def _run_orchestrated(self, goal, url, tab_id):
        from .agent import Agent
        from .orchestrator import run_orchestration
        from .skills import select_skills
        from .tools import ToolBox

        live = {"text": "", "at": 0.0}

        def on_step(step):
            live["text"] = ""  # a new step begins: reset the live thinking buffer
            if self.orchestrated is not None:
                self.orchestrated["log"] = (self.orchestrated.get("log") or [])[-40:]
                self.orchestrated["log"].append(step)
            self._broadcast()

        def on_delta(chunk):
            live["text"] = (live["text"] + chunk)[-600:]
            now = time.monotonic()
            if now - live["at"] >= DELTA_BROADCAST_INTERVAL:
                live["at"] = now
                self.bridge.broadcast({"type": "delta", "text": live["text"]})

        def browser_runner(browser_goal, browser_url=None):
            """Delegates a browser step to the fast JEV Agent on the live tab."""
            target = browser_url or url or "about:blank"
            try:
                browser = self._browser(target, tab_id)
                self.agent = Agent(target, str(browser_goal), screenshots=True, browser=browser)
                self._audited_steps = 0
                self._broadcast()
                for _state in self.agent.run():
                    self._audit_steps(_state if isinstance(_state, dict) else self.agent.snapshot())
                    self._broadcast()
                    if self.stopped:
                        break
                snap = self.agent.snapshot()
            except (ValueError, RuntimeError, BridgeError, StalePage) as error:
                self.agent = None
                self.last_error = str(error)
                return f"BROWSER ERROR: {error}"
            finally:
                self.agent = None
            self.last_error = None
            page = snap.get("page") or {}
            history = [str(item.get("action") or item.get("decision") or item) for item in snap.get("history", [])[-5:]]
            summary = "BROWSER RESULT: status={}, url={}\nRecent actions: {}".format(
                snap.get("status"), page.get("url"), "; ".join(history) or "(none)"
            )
            text = (page.get("text") or "")[:1200]
            return summary + "\nPage excerpt:\n" + text if text else summary

        try:
            gate = ApprovalGate(self.bridge)
            self.approvals = gate
            center = permissions.PermissionCenter(
                gate.request,
                on_decision=lambda tool, args=None, **extra: self._audit_event(tool, args, **extra),
            )
            toolbox = ToolBox(
                self._workspace, request_approval=gate.request, browser_runner=browser_runner,
                trace_id=self._trace, permission_center=center,
            )
            selected = select_skills(goal, available_tools=toolbox.registry)
            self.orchestrated["skills"] = [skill["id"] for skill in selected]
            self._broadcast()
            result = run_orchestration(goal, toolbox, on_step=on_step, on_delta=on_delta)
            self.orchestrated.update(
                status="done" if not self.stopped else "stopped",
                final=result["final"],
                usage=result.get("usage"),
                latency_ms=result.get("latency_ms"),
            )
        except (ValueError, RuntimeError, BridgeError, StalePage) as error:
            self.last_error = redact(str(error))
            if self.orchestrated is not None:
                self.orchestrated["status"] = "error"
            self.bridge.broadcast({"type": "error", "message": redact(str(error))})
        finally:
            self.approvals = None
            self.orchestrated = self.orchestrated or {}
            self.orchestrated.setdefault("log", [])
            self.orchestrated["status"] = self.orchestrated.get("status") or "stopped"
            self._record_run(
                "orchestrated", goal,
                status=self.orchestrated.get("status") or "stopped",
                steps=len(self.orchestrated.get("log") or []),
                elapsed_ms=self.orchestrated.get("latency_ms") or 0,
            )
            self._broadcast()
            self._lock.release()

    # ── sidebar model/parameter handlers (MVP-1) ──────────────────────────────

    def handle_models(self, refresh=False):
        from . import discovery

        cached = discovery._load_registry()

        def send_registry():
            try:
                registry = discovery.discover(refresh=bool(refresh) or cached is None)
                self.bridge.send({"type": "models", "registry": registry})
            except Exception as error:  # noqa: BLE001 - report, never crash the bridge
                self.bridge.send({"type": "models", "registry": cached, "error": str(error)[:300]})

        if cached:
            self.bridge.send({"type": "models", "registry": cached})
        else:
            self.bridge.send({"type": "models", "registry": None, "loading": True})
        threading.Thread(target=send_registry, daemon=True).start()

    def handle_model_select(self, message):
        role = message.get("role", "")
        provider_name = str(message.get("provider", "")).strip()
        model_id = str(message.get("model", "")).strip()
        if role == "policy" and not model_id:
            # the sidebar's built-in option: return to the TypeSafe Jev policy
            if self._typesafe_key:
                os.environ["TYPESAFE_API_KEY"] = self._typesafe_key
            os.environ.pop("POLICY_PROVIDER", None)
            os.environ.pop("POLICY_MODEL", None)
            config = load_config()
            (config.get("models") or {}).pop("policy", None)
            save_config(config)
            self._broadcast()
            threading.Thread(target=self.run_provider_check, daemon=True).start()
            return
        if role == "policy" and self._typesafe_key:
            os.environ.pop("TYPESAFE_API_KEY", None)  # an explicit UI switch overrides the .env default
        try:
            removed = apply_model(role, provider_name, model_id)  # prunes values the new model lacks
        except (ValueError, KeyError) as error:
            self.bridge.send({"type": "error", "message": redact(f"Could not switch model: {error}")})
            return
        if removed:
            # e.g. GLM → Kimi: top_p and the penalties do not exist there
            self.bridge.send({
                "type": "notice",
                "message": f"{', '.join(sorted(removed))} cleared — the new model does not expose it.",
            })
        self._broadcast()
        threading.Thread(target=self.run_provider_check, daemon=True).start()

    def handle_model_probe(self, message):
        """Run the live compatibility probe for one role's model (spec §4.1, step 5)."""
        role = str(message.get("role", "")).strip()
        try:
            provider_name, model_id = model_for(role)
        except (KeyError, ValueError):
            self.bridge.send({"type": "error", "message": f"Unknown role: {role}"})
            return
        if not model_id:
            self.bridge.send({"type": "error", "message": f"No model selected for {role}."})
            return

        def run_probe():
            from . import discovery

            try:
                report = discovery.probe_model(provider_name, model_id)
            except RuntimeError as error:
                self.bridge.send({"type": "error", "message": redact(f"Probe failed: {error}")})
                return
            self.bridge.send({"type": "probe", "role": role, "report": report})
            try:
                prune_params(role)
            except Exception:  # noqa: BLE001 - pruning is best effort
                pass
            self._broadcast()

        self.bridge.send({"type": "probe", "role": role, "loading": True})
        threading.Thread(target=run_probe, daemon=True).start()

    def handle_profile(self, action, name):
        try:
            if action == "save":
                save_profile(name)
            elif action == "apply":
                apply_profile(name)
            elif action == "delete":
                delete_profile(name)
            else:
                raise ValueError(f"Unknown profile action: {action}")
        except (ValueError, KeyError) as error:
            self.bridge.send({"type": "error", "message": redact(f"Profile error: {error}")})
            return
        self._broadcast()
        if action == "apply":
            threading.Thread(target=self.run_provider_check, daemon=True).start()

    def _record_run(self, mode, goal, status, steps, elapsed_ms):
        """Append one line to the local run history (artifacts/runs.jsonl)."""
        try:
            RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with RUNS_LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": time.time(),
                    "trace": self._trace,
                    "mode": mode,
                    "goal": goal[:200],
                    "status": status,
                    "steps": steps,
                    "elapsed_ms": elapsed_ms,
                    "tokens": self._tokens(),
                    "error": self.last_error,
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass  # the history log must never break a run

    def handle_params_set(self, message):
        preset = message.get("preset")
        try:
            if preset:
                apply_preset(str(preset))
            else:
                apply_params(str(message.get("role", "")), message.get("params") or {})
        except (ValueError, KeyError) as error:
            self.bridge.send({"type": "error", "message": redact(f"Could not apply parameters: {error}")})
            return
        self._broadcast()

    def _broadcast(self):
        self.bridge.broadcast({"type": "state", "state": self.current_state()})


def load_environment():
    """Read the key file (one location for every launch path: see providers.env_file_path)."""
    from . import providers as provider_layer

    return provider_layer.load_env_file()


def main():
    global _SERVER
    load_environment()
    from . import providers as provider_layer

    # No terminal prompt here on purpose: the setup is done in Firefox. The host
    # starts regardless, the sidebar carries the key form, and `ensure_configured`
    # stays for the terminal-first paths (the demo GUI, scripts, CI).
    port = int(os.environ.get("FIREFOX_BRIDGE_PORT", str(DEFAULT_PORT)))
    token = os.environ.get("FIREFOX_BRIDGE_TOKEN") or None
    _SERVER = BridgeServer(port=port, token=token)
    _SERVER.runner = TaskRunner(_SERVER)
    _SERVER.start()
    print(f"AI Agent for Firefox bridge: ws://127.0.0.1:{_SERVER.port}", flush=True)
    print("Load extension/ in Firefox via about:debugging → This Firefox → Load Temporary Add-on.", flush=True)
    print(
        f"Keys are read from {provider_layer.env_file_display()} "
        "(paste one in the sidebar and it lands there).",
        flush=True,
    )
    if provider_layer.is_configured():
        print(f"Policy model: {policy_description()}", flush=True)
    else:
        print("No API key yet — open the agent sidebar in Firefox and paste one there; it takes 2 minutes.", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
