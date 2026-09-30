"""Regresión de los parches P0 de v0.13.0: H8, H10, H3 y H1/H2.

Cada test de este módulo falla contra el tag v0.13.0 publicado y pasa con los parches.
Se probea el puente con sockets crudos, igual que en la verificación en vivo del informe:
es el único modo de comprobar la política de origen sin el navegador detrás.
"""

import base64
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from jev_ultrafast.browser import target_expression
from jev_ultrafast.firefox import BridgeServer

REPO = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# H8 · la acción debe llegar a la arrow function como literal de objeto
# ─────────────────────────────────────────────────────────────────────────────


def _argument_of(expression):
    """El argumento que la arrow function recibe, sin la paréntesis de cierre final."""
    return expression[expression.rindex("})(") + 3 : -1]


def test_target_expression_passes_an_object_not_a_quoted_string():
    """El bug: el JSON de la acción se inyectaba entre comillas, así que el literal
    se cerraba en su primera comilla interna y el argumento llegaba como string."""
    expression = target_expression({"node": 7, "kind": "click", "value": ""})
    argument = _argument_of(expression)
    assert not argument.startswith('"'), f"la acción llega como string, no como objeto: {argument}"
    # Y tiene que seguir siendo el objeto que el bucle construyó.
    assert json.loads(argument) == {"node": 7, "kind": "click", "value": ""}


def test_target_expression_survives_a_value_containing_quotes():
    """Un valor tecleado con comillas no puede romper el literal."""
    action = {"node": 3, "kind": "fill", "value": 'Zúrich "ZRH" \\ oreja'}
    argument = _argument_of(target_expression(action))
    assert json.loads(argument) == action


@pytest.mark.skipif(not shutil.which("node"), reason="node no está instalado")
def test_target_expression_is_valid_javascript():
    """La comprobación que 585 tests unitarios nohacían: ¿el navegador acepta la expresión?

    Contra el tag publicado, node --check responde SyntaxError: missing ) after argument list.
    """
    expression = target_expression({"node": 7, "kind": "click", "value": ""})
    script = Path("/tmp/jev_target_expression_check.js")
    script.write_text(f"const resolve = {expression};\n", encoding="utf-8")
    try:
        result = subprocess.run(
            ["node", "--check", str(script)], capture_output=True, text=True, timeout=30, check=False
        )
    finally:
        script.unlink(missing_ok=True)
    assert result.returncode == 0, f"JavaScript inválido:\n{result.stderr}"


def test_the_broken_form_would_really_have_been_a_syntax_error():
    """Fija el modo de fallo, para que nadie reintroduca la forma entrecomillada."""
    broken = 'const resolve = (a => { return a.x; })("' + json.dumps({"node": 7}) + '");'
    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")
    script = Path("/tmp/jev_broken_form.js")
    script.write_text(broken, encoding="utf-8")
    try:
        result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True, timeout=30, check=False)
    finally:
        script.unlink(missing_ok=True)
    assert result.returncode != 0
    assert "SyntaxError" in result.stderr


# ─────────────────────────────────────────────────────────────────────────────
# H3 · el puente rechaza los handshakes sin Origin
# ─────────────────────────────────────────────────────────────────────────────


def _handshake(port, origin=None, timeout=5.0):
    """Un handshake WebSocket real contra el puente, con el Origin que se le pida."""
    key = base64.b64encode(b"0123456789abcdef").decode()
    lines = [
        "GET / HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    if origin is not None:
        lines.append(f"Origin: {origin}")
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
        conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        return conn.recv(64)


def test_bridge_refuses_a_handshake_without_an_origin():
    """H3: sin Origin, la respuesta pasa de 101 a 403.

    Firefox envía Origin siempre en un handshake WebSocket; ningún proceso local lo hace,
    así que su ausencia delata a un atacante local en vez de a un cliente legítimo.
    """
    server = BridgeServer(port=0)
    server.start()
    try:
        status = _handshake(server.port, origin=None)
    finally:
        server.close()
    assert b"403" in status, f"un handshake sin Origin debe rechazarse, llegó: {status!r}"


def test_bridge_still_accepts_the_extension_origin():
    """El camino bueno no se rompe: la extensión de Firefox sigue entrando."""
    server = BridgeServer(port=0)
    server.start()
    try:
        status = _handshake(server.port, origin="moz-extension://abcd-1234")
    finally:
        server.close()
    assert b"101" in status, f"la extensión debe poder conectarse, llegó: {status!r}"


def test_bridge_still_refuses_a_web_page_origin():
    """Una web maliciosa nunca pudo entrar; sigue sin poder."""
    server = BridgeServer(port=0)
    server.start()
    try:
        status = _handshake(server.port, origin="https://evil.example")
    finally:
        server.close()
    assert b"403" in status, f"una web no puede conectarse, llegó: {status!r}"


# ─────────────────────────────────────────────────────────────────────────────
# H10 · una navegación lenta no puede matar la misión
# ─────────────────────────────────────────────────────────────────────────────


class _SlowNavigationBrowser:
    """Un navegador cuya navegación tarda varias observaciones en estabilizarse.

    Es lo que pasa de verdad: una búsqueda en Wikipedia deja el documento cambiando
    durante segundos, y una re-observación inmediata lo declaraba muerto.
    """

    def __init__(self, settle_after=6):
        self.settle_after = settle_after
        self.observes = 0
        self.acts = 0
        self.closed = False

    def _page(self):
        from jev_ultrafast.browser import fingerprint

        page = {
            "url": "https://example.test/",
            "title": "Search",
            "text": "Search",
            "scroll": {"y": 0},
            "actions": [
                {"id": "e1", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            ],
            "screenshot": "",
        }
        page["fingerprint"] = fingerprint(page)
        return page

    def observe(self, screenshot=False):
        self.observes += 1
        if self.observes < self.settle_after:
            from jev_ultrafast.browser import StalePage

            raise StalePage("Document changed during evaluation")
        return self._page()

    def fresh(self, page):
        return True

    def act(self, action, page, text=None):
        """La acción sobre una página que todavía se mueve: siempre obsoleta."""
        from jev_ultrafast.browser import StalePage

        self.acts += 1
        raise StalePage("Target changed or is covered. Observe again.")

    def close(self):
        self.closed = True


def _agent_with(browser):
    """Un Agent real, con su estado completo y el modelo de decisión sustituido."""
    from jev_ultrafast import agent as agent_module

    agent = object.__new__(agent_module.Agent)
    agent.browser = browser
    agent.screenshots = False
    agent.pending_text = None
    agent.planner = None
    agent.record_dir = None
    page = browser._page()
    agent.state = {
        "browser": browser,
        "goal": "open the page",
        "page": page,
        "decision": None,
        "history": [],
        "status": "ready",
        "plan": ["open the page"],
        "plan_index": 0,
        "replans": 0,
        "planner": None,
        "decisions": [],
        "text_calls": [],
        "elapsed_ms": 0,
        "started_at": time.perf_counter(),
        "record": False,
    }
    return agent


def _stub_the_policy(monkeypatch):
    """El ejecutor siempre elige el mismo elemento: la prueba es del reloj, no del modelo."""
    from jev_ultrafast import agent as agent_module

    decision = {
        "choice": "e1",
        "operation": "CLICK",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {"e1": 1.0},
        "latency_ms": 10,
        "usage": {},
    }
    monkeypatch.setattr(agent_module, "choose", lambda *_args, **_kwargs: dict(decision))


def test_a_slow_navigation_is_waited_out_instead_of_ending_the_run(monkeypatch):
    """H10: re-observar una sola vez abandonaba la misión en pleno tránsito.

    El bucle ahora espera a que la página se estabilice antes de devolver la decisión,
    en vez de matarla en el primer cambio de documento.
    """
    from jev_ultrafast import agent as agent_module

    monkeypatch.setattr(agent_module.time, "sleep", lambda _seconds: None)
    _stub_the_policy(monkeypatch)
    browser = _SlowNavigationBrowser(settle_after=6)
    agent = _agent_with(browser)

    state = agent_module.Agent.command(agent, "tick")

    assert browser.observes >= 6, f"debería reintentar hasta que la página se calme, observó {browser.observes} veces"
    assert state["status"] == "ready", "una navegación lenta no puede dejar la misión bloqueada"
    assert state["decision"] is None, "la decisión se consume una vez, también tras reintentar"


def test_a_page_that_never_settles_still_gives_up(monkeypatch):
    """Esperar no puede volverse esperar para siempre: pasado el plazo, el error se propaga."""
    from jev_ultrafast import agent as agent_module
    from jev_ultrafast.browser import StalePage

    monkeypatch.setattr(agent_module, "OBSERVE_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(agent_module.time, "sleep", lambda _seconds: None)
    _stub_the_policy(monkeypatch)
    browser = _SlowNavigationBrowser(settle_after=10_000)
    agent = _agent_with(browser)

    with pytest.raises(StalePage):
        agent_module.Agent.command(agent, "tick")
    assert browser.observes >= 1


# ─────────────────────────────────────────────────────────────────────────────
# H1/H2 · la documentación dice lo que el código hace
# ─────────────────────────────────────────────────────────────────────────────


def test_no_user_facing_doc_names_the_replaced_default_model():
    """H2: el README describía un modelo que el código ya no envía.

    El CHANGELOG es el registro histórico y se queda; los starters y parameters.py
    contienen la maquinaria de migración, que necesita nombrar el modelo viejo.
    """
    replaced = "z-ai/glm-5.3-flash"
    spared = {"CHANGELOG.md", "start-host.sh", "start-host.bat", "start-host.command"}
    offenders = []
    for path in REPO.rglob("*"):
        if path.suffix not in {".md", ".yml"} or not path.is_file():
            continue
        if ".git/" in str(path) or path.name in spared or "docs/archive" in str(path):
            continue
        if replaced in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, f"documentación o workflow que aún nombra el modelo retirado: {offenders}"


def test_the_derived_default_matches_what_the_readme_says():
    """El README y DERIVED_MODELS tienen que contar la misma historia."""
    from jev_ultrafast.parameters import NEW_DEFAULT_MODEL
    from jev_ultrafast.providers import DERIVED_MODELS

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert NEW_DEFAULT_MODEL in readme, "el README no nombra el modelo que el código deriva"
    for role in ("planner", "policy", "text"):
        assert DERIVED_MODELS["nvidia"][role] == NEW_DEFAULT_MODEL, f"el rol {role} deriva otro modelo"


def test_the_readme_does_not_claim_a_stale_test_count():
    """H1: el README declaraba 413 tests mientras la suite ejecutaba 585."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "413 tests" not in readme
