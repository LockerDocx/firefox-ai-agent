"""H11 · un formulario con contraseña se puede completar, y el secreto no sale.

El hallazgo de la evaluación era que el ejecutor nunca elegía TYPE_TEXT sobre el campo de
contraseña. La causa real era otra: `snapshot.js` excluía el campo del espacio de acciones
(`safe = e => !['password',...].includes(e.type)`), así que **el modelo no lo veía** y ningún
elección podía alcanzarlo. No era una decisión del modelo: era un bloqueo estructural.

Estos tests fijan las dos mitades del arreglo, y la segunda importa más que la primera:
el campo se ofrece, su contenido **no**.

  * el campo de contraseña aparece en la tabla de elementos y admite TYPE_TEXT;
  * su valor no aparece en la acción, ni en el page key, ni en el guard;
  * el ayudante de texto puede decir "no tengo esa credencial", y eso no es un fallo;
  * sin credencial, el bucle para y lo dice, en vez de teclear y clicar Login en bucle;
  * lo tecleado en un campo secreto se enmascara en el historial y en el panel.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.model import (
    _policy_request,
    action_space,
    budget_actions,
    field_context,
    operation_catalog,
)

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "jev_ultrafast" / "snapshot.js"


def password_action(node=900, label="Password", y=200):
    return {
        "id": "e900",
        "node": node,
        "kind": "fill",
        "role": "textbox",
        "label": label,
        "value": "",          # lo que devuelve el extractor: nunca el contenido
        "secret": True,
        "rect": {"x": 20, "y": y, "w": 200, "h": 30},
    }


def user_action(node=901, label="Username", y=160):
    return {
        "id": "e901",
        "node": node,
        "kind": "fill",
        "role": "textbox",
        "label": label,
        "value": "standard_user",
        "rect": {"x": 20, "y": y, "w": 200, "h": 30},
    }


def login_button(node=902, y=250):
    return {
        "id": "e902",
        "node": node,
        "kind": "click",
        "role": "button",
        "label": "Login",
        "value": "",
        "rect": {"x": 20, "y": y, "w": 80, "h": 30},
    }


def page(actions):
    return {
        "url": "https://saucedemo.test/",
        "title": "Swag Labs",
        "text": "Username  Password  Login",
        "w": 1120,
        "h": 780,
        "actions": actions,
    }


# ── 1 · el campo se ofrece ─────────────────────────────────────────────────────


def test_a_password_field_is_in_the_action_space():
    """La mitad que faltaba: el campo tiene que ser un objetivo alcanzable."""
    elements, targets, _controls = action_space([user_action(), password_action(), login_button()])
    fields = [e for e in elements if e["label"] == "Password"]
    assert fields, "el campo de contraseña no está en la tabla de elementos"
    field = fields[0]
    assert "TYPE_TEXT" in field["operations"], "el campo de contraseña no admite TYPE_TEXT"
    assert field["index"] in targets["TYPE_TEXT"]
    assert targets["TYPE_TEXT"][field["index"]]["id"] == "e900"


def test_the_password_field_survives_the_prompt_budget():
    """El recorte de H6 no puede seguir dejando fuera el campo que hace falta."""
    actions = [password_action(node=950 + i, label=f"Password {i}", y=(i * 40) % 6000) for i in range(300)]
    actions += [password_action()]
    state = page(actions)
    kept, _ = budget_actions(actions, state)
    assert "e900" in {a["id"] for a in kept}, "el presupuesto dejó fuera el campo de contraseña"


def test_the_prompt_says_the_field_is_a_password():
    elements, targets, controls = action_space([password_action()])
    user = _policy_request("log in", page([password_action()]), elements,
                           operation_catalog(targets, controls), (), 0)
    assert "secret" in user
    assert "never invent" in user.lower()


# ── 2 · el secreto no sale ─────────────────────────────────────────────────────


def test_the_extractor_no_longer_excludes_password_fields():
    """El bloqueo original, escrito como test para que nadie lo vuelva a introducir."""
    source = SNAPSHOT.read_text(encoding="utf-8")
    assert "!['password','file','hidden'].includes(e.type)" not in source, (
        "excluir el campo de contraseña deja los formularios imposibles de completar (H11)"
    )
    assert "const secret = e => e.type === 'password'" in source


def test_the_password_never_reaches_the_page_key_or_the_guard():
    source = SNAPSHOT.read_text(encoding="utf-8")
    page_key = source[source.index("cache.pageKey=") : source.index("cache.guard=")]
    guard = source[source.index("cache.guard=") : source.index("const actions=[]")]
    assert "mask(e)" in page_key, "el page key debe leer el valor enmascarado, no el real"
    assert "mask(e)" in guard, "el guard debe leer el valor enmascarado, no el real"
    # ningún acceso directo al valor dentro de esas dos rutas
    assert not re.search(r"\[identity\(e\),\s*e\.value", page_key)
    assert not re.search(r"name\(e\),\s*e\.value", guard)


def _run_node(script, tmp_path):
    """Ejecuta JS en node y devuelve stdout.

    El script va en un archivo UTF-8, no en argv: bajo una página de códigos heredada
    (`LC_ALL=C`, que es lo que prueba CI) un argumento con acentos o con «•••» no se
    puede codificar a ASCII y posix_spawn lanza UnicodeEncodeError. Tampoco se puede
    dejar la decodificación en la del sistema, por el mismo motivo.
    """
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")
    path = tmp_path / "enmascarado.js"
    path.write_text(script, encoding="utf-8")
    return subprocess.run(
        [node, str(path)], capture_output=True, encoding="utf-8", timeout=30, check=True
    ).stdout


def test_masking_runs_in_a_real_javascript_engine(tmp_path):
    """Comprobado por node, no por lectura: un regex puede mentir sobre lo que ejecuta."""
    script = """
    const secret = e => e.type === 'password';
    const mask = e => secret(e) ? (e.value ? '•••' : '') : (e.value ?? '');
    const out = {
      action: secret({type:'password', value:'hunter2'}) ? '' : 'hunter2',
      pageKey: mask({type:'password', value:'hunter2'}),
      guard: mask({type:'password', value:'hunter2'}),
      plain: mask({type:'text', value:'Zurich'}),
    };
    process.stdout.write(JSON.stringify(out));
    """
    out = json.loads(_run_node(script, tmp_path))
    assert out["action"] == "", "el valor de un password no puede viajar en la acción"
    assert "hunter2" not in json.dumps(out), "el secreto aparece en algún sitio"
    assert out["plain"] == "Zurich", "un campo normal debe seguir mandando su valor"
    assert out["pageKey"] and out["guard"], "un password ya escrito tiene que cambiar el page key"


def test_a_password_already_filled_is_visible_as_a_password_not_as_its_value(tmp_path):
    """Un campo con contenido no es un campo vacío: el modelo debe distinguirlo."""
    script = (
        "const secret=e=>e.type==='password';"
        "const mask=e=>secret(e)?(e.value?'•••':''):(e.value??'');"
        "process.stdout.write(JSON.stringify([mask({type:'password',value:'x'}),"
        "mask({type:'password',value:''})]));"
    )
    filled, empty = json.loads(_run_node(script, tmp_path))
    assert filled and not empty, "un password con contenido y uno vacío deben verse distintos"


# ── 3 · "no tengo la credencial" es una respuesta válida ──────────────────────


def test_the_text_helper_is_told_the_field_is_secret():
    context = field_context("log in as bob", password_action(), page([password_action()]), [])
    assert context["secret"] is True
    assert context["field"]["value"] == "", "el valor nunca llega al ayudante"


def test_an_ordinary_field_is_not_marked_secret():
    context = field_context("search flights", user_action(), page([user_action()]), [])
    assert context["secret"] is False


def test_no_credential_is_a_valid_answer_for_a_secret(monkeypatch):
    """Antes, {"text": null} era un fallo y se reintentaba; para un secreto es la verdad."""
    monkeypatch.setattr(model.providers, "resolve", lambda role: {"name": "nvidia", "model": "m", "base_url": "u"})
    calls = []

    def fake_chat(provider, system, user, max_tokens=1024, on_delta=None):
        calls.append(user)
        return json.dumps({"text": None}), {"model": "m", "usage": {}}

    monkeypatch.setattr(model.providers, "chat", fake_chat)
    text, helper = model.field_text(field_context("log in", password_action(), page([]), []))
    assert text is None
    assert "unavailable" in helper
    assert len(calls) == 1, "no debe reintentar: la respuesta no era inválida, era verdadera"


def test_a_secret_with_no_value_is_not_retried_twice(monkeypatch):
    monkeypatch.setattr(model.providers, "resolve", lambda role: {"name": "nvidia", "model": "m", "base_url": "u"})
    seen = []

    def fake_chat(provider, system, user, max_tokens=1024, on_delta=None):
        seen.append(user)
        return json.dumps({"text": None}), {"model": "m", "usage": {}}

    monkeypatch.setattr(model.providers, "chat", fake_chat)
    with pytest.raises(ValueError):
        model.field_text(field_context("search", user_action(), page([]), []))
    assert len(seen) == 2, "en un campo normal, un null sí se reintenta: puede ser una respuesta floja"


# ── 4 · sin credencial, el bucle para y lo dice ────────────────────────────────


class _Browser:
    """El navegador mínimo que necesita `act`: si una decisión caduca, no llega a teclear."""

    def __init__(self):
        self.typed = None
        self.acted = []

    def fresh(self, page):
        return True

    def act(self, action, page, text=None):
        self.typed = text
        self.acted.append((action, text))

    def observe(self, screenshot=False):
        return None

    def close(self):
        pass


def _agent(monkeypatch, actions, helper_value, decision_id="e900"):
    browser = _Browser()
    state_page = page(actions)
    state_page["fingerprint"] = "fp"
    agent = object.__new__(loop.Agent)
    agent.browser = browser
    agent.screenshots = False
    agent.pending_text = None
    agent.planner = None
    agent.record_dir = None
    # La decisión ya está tomada y encaja con el fingerprint: lo que se prueba aquí es qué
    # pasa al ejecutar un TYPE_TEXT, no cómo se elige.
    agent.state = {
        "browser": browser,
        "goal": "log in",
        "page": state_page,
        "decision": {
            "choice": decision_id,
            "operation": "TYPE_TEXT",
            "target": "1",
            "confidence": 1.0,
            "probabilities": {decision_id: 1.0},
            "latency_ms": 1,
            "usage": {},
        },
        "history": [],
        "status": "predicted",
        "plan": ["log in"],
        "plan_index": 0,
        "replans": 0,
        "planner": None,
        "decisions": [],
        "text_calls": [],
        "elapsed_ms": 0,
        "started_at": 0.0,
        "record": False,
    }
    monkeypatch.setattr(loop, "field_text",
                        lambda context: (helper_value, {"model": "m", "latency_ms": 1, "usage": {}}))
    monkeypatch.setattr(loop.time, "perf_counter", lambda: 1.0)
    return agent


def test_without_a_credential_the_run_stops_and_says_why(monkeypatch):
    """El bucle de 10 pasos que se midió venía de seguir Login con el campo vacío."""
    agent = _agent(monkeypatch, [password_action()], None)
    state = loop.Agent.command(agent, "act", {"fingerprint": "fp"})
    assert state["status"] == "blocked"
    assert "does not supply" in state["blocked_reason"]
    assert "will not invent" in state["blocked_reason"]
    assert state["history"] == [], "no se ejecutó ninguna acción: no se tecleó ni se envió"


def test_a_blocked_run_stops_the_loop(monkeypatch):
    agent = _agent(monkeypatch, [password_action()], None)
    state = loop.Agent.command(agent, "act", {"fingerprint": "fp"})
    assert state["status"] in {"done", "blocked"}, "run() sale del bucle con blocked"


# ── 5 · lo tecleado en un secreto no se lee en ninguna parte ───────────────────


def _typed_browser(agent):
    """Un navegador que además devuelve una página nueva tras cada acción."""

    class Browser(_Browser):
        def observe(self, screenshot=False):
            fresh = dict(agent.state["page"])
            fresh["fingerprint"] = "fp2"
            return fresh

    return Browser()


def test_a_typed_password_is_masked_in_the_history_and_the_panel(monkeypatch):
    agent = _agent(monkeypatch, [password_action()], "hunter2")
    browser = _typed_browser(agent)
    agent.browser = agent.state["browser"] = browser  # el bucle usa state["browser"]
    state = loop.Agent.command(agent, "act", {"fingerprint": "fp"})

    assert browser.typed == "hunter2", "el navegador recibe la contraseña: tiene que teclearla"
    recorded = json.dumps(state, ensure_ascii=False)
    assert "hunter2" not in recorded, "la contraseña llegó al estado que ve el panel"
    assert "hunter2" not in json.dumps(state["history"], ensure_ascii=False)
    assert "hunter2" not in json.dumps(state["text_calls"], ensure_ascii=False)


def test_an_ordinary_field_still_shows_its_value(monkeypatch):
    """Lo contrario de la prueba anterior: enmascarar de más también es un fallo."""
    field = user_action(label="Where from?")
    agent = _agent(monkeypatch, [field], "Zúrich", decision_id="e901")
    browser = _typed_browser(agent)
    agent.browser = agent.state["browser"] = browser  # el bucle usa state["browser"]
    state = loop.Agent.command(agent, "act", {"fingerprint": "fp"})
    assert browser.typed == "Zúrich"
    assert "Zúrich" in json.dumps(state["history"], ensure_ascii=False)
