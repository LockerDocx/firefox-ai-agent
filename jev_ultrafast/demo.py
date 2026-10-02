"""Loopback-only inspector for the Firefox browser agent."""

import argparse
import atexit
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .agent import Agent
from .model import policy_description
from .questions import MAX_STEPS

ROOT = Path(__file__).parent
PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
AGENT = None
SCENARIOS = ("travel", "research", "flights")


def version():
    """La versión instalada, o la del código si se está ejecutando desde el checkout."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed

    try:
        return installed("jev-ultrafast")
    except PackageNotFoundError:  # pragma: no cover - solo desde un checkout sin instalar
        return "0.0.0+source"


def parse_arguments(argv=None):
    """H7: `jev --help` imprimía nada y se quedaba vivo hasta que el timeout lo mataba.

    No era un fallo de argparse —es que no había argparse—. `main()` levantaba el servidor
    antes de mirar los argumentos, así que `--help` no tenía a quién preguntarle: arrancaba el
    demo, servía en el 8766 y esperaba. Ahora los argumentos se leen **antes** de levantar
    nada, y `--help` sale con 0 como cualquier herramienta.
    """
    parser = argparse.ArgumentParser(
        prog="jev",
        description="Loopback-only inspector for the Firefox browser agent.",
        epilog=(
            "With no arguments it starts the demo on 127.0.0.1 and waits for the browser. "
            "Press Ctrl+C to stop."
        ),
    )
    parser.add_argument("--version", action="version", version=f"jev {version()}")
    parser.add_argument(
        "--port", type=int, default=PORT,
        help=f"puerto del demo (por defecto {PORT}; TYPESAFE_DEMO_PORT hace lo mismo)",
    )
    parser.add_argument(
        "--scenario", choices=SCENARIOS, default="flights",
        help="escenario con el que se abre el demo en el navegador",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="no abrir el navegador al arrancar",
    )
    return parser.parse_args(argv)


def load_environment():
    """Same key file as the Firefox host: one location, however the GUI is started."""
    from . import providers as provider_layer

    return provider_layer.load_env_file()


def response_state():
    state = AGENT.snapshot() if AGENT else {"page": None, "status": "idle", "history": [], "decision": None}
    return {
        **state,
        "text_model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "policy_model": policy_description(),
        "max_steps": MAX_STEPS,
    }


def close_browser():
    global AGENT
    if AGENT:
        AGENT.close()
        AGENT = None


def command(name, body):
    global AGENT
    if name == "reset":
        scenario = body.get("scenario", "flights")
        if scenario not in {"travel", "research", "flights"}:
            raise ValueError("Unknown demo scenario")
        goal = body.get("goal", "").strip()
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        close_browser()
        AGENT = Agent(
            "https://www.google.com/travel/flights?hl=en"
            if scenario == "flights"
            else f"{ORIGIN}/fixture.html?scenario={scenario}",
            goal,
            screenshots=True,
            record_dir=Path.cwd() / "artifacts" / "frames" if body.get("record") else None,
        )
        AGENT.state["scenario"] = scenario
    else:
        if AGENT is None:
            raise ValueError("Start a demo first")
        AGENT.command(name, body)
    return response_state()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, mime="application/json"):
        content = content if isinstance(content, bytes) else content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            return self.send(403, "Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                return self.send(200, json.dumps(response_state()))
        if path == "/demo.mp4":
            video = ROOT.parent / "docs" / "demo.mp4"
            if video.exists():
                return self.send(200, video.read_bytes(), "video/mp4")
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/fixture.html": ("fixture.html", "text/html"),
        }
        if path not in files:
            return self.send(404, "Not found", "text/plain")
        name, mime = files[path]
        content = (ROOT / "static" / name).read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
        self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self):
        if (
            self.headers.get("Host") != f"127.0.0.1:{PORT}"
            or self.headers.get("X-Demo-Token") != TOKEN
            or self.headers.get("Origin") not in (None, ORIGIN)
        ):
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        if not LOCK.acquire(blocking=False):
            return self.send(409, json.dumps({"error": "A browser step is already running"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = command(self.path.removeprefix("/api/"), body)
            self.send(200, json.dumps(result))
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.send(400, json.dumps({"error": str(error)}))
        except Exception:
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args):
        pass


def main(argv=None):
    """H7: los argumentos se leen antes de levantar el servidor, no después.

    `ORIGIN` se recalcula con el puerto pedido porque el handler valida la cabecera Host, así
    que un `--port` que no se propagara haría que el propio panel se rechazara a sí mismo.
    """
    global ORIGIN
    args = parse_arguments(argv)
    ORIGIN = f"http://127.0.0.1:{args.port}"

    load_environment()
    from . import providers as provider_layer

    if not provider_layer.ensure_configured() and sys.stdin.isatty():
        raise SystemExit(1)
    atexit.register(close_browser)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"AI Agent for Firefox: {ORIGIN}", flush=True)
    print(f"Policy model: {policy_description()}", flush=True)
    if not args.no_open:
        try:
            webbrowser.open(f"{ORIGIN}/?scenario={args.scenario}")
        except Exception:  # una máquina sin navegador no es un motivo para no servir
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
