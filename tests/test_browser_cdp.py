"""H4 · la mitad de `browser.py` que toca el CDP de verdad, probada sin navegador.

`browser.py` estaba al 47 % de cobertura, y esa mitad no era la que sobraba: era la que
escondió el defecto central de v0.13.1. La suite de 585 pruebas sustituía la capa CDP entera,
así que una línea que construía mal una expresión de JavaScript pasaba en verde y mataba cada
clic, cada relleno y cada selección en la máquina del usuario.

Aquí no se abre un navegador: se sustituye `cdp()` por un grabador y se comprueba **qué
llamadas se harían**. Eso no prueba que el navegador obedezca —para eso hace falta la
batería viva— pero sí prueba la mitad que se rompió: la que decide qué se le pide.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jev_ultrafast import browser as cdp_browser
from jev_ultrafast.browser import Browser, StalePage, browser_operation, fingerprint, target_expression

ROOT = Path(__file__).resolve().parent.parent


class Recorder:
    """Sustituye a `cdp()`: guarda cada llamada y devuelve lo que el test le pida.

    Una respuesta puede ser una LISTA de valores, que se van entregando en orden y el último
    se repite. Hace falta porque una sola acción hace dos evaluaciones encadenadas —primero
    la de frescura, luego la del objetivo— y un grabador que devolviera siempre lo mismo
    devolvería la respuesta de la primera también donde se espera la segunda.
    """

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}
        self.position = {}

    def __call__(self, method, session_id=None, **params):
        self.calls.append((method, params))
        answer = self.answers.get(method)
        if isinstance(answer, list):
            index = self.position.get(method, 0)
            self.position[method] = index + 1
            answer = answer[min(index, len(answer) - 1)]
        return answer() if callable(answer) else answer

    def reset(self):
        self.position.clear()

    def methods(self):
        return [method for method, _ in self.calls]

    def params_for(self, method):
        return [params for name, params in self.calls if name == method]


def _observe_request(**overrides):
    request = {"operation": "observe", "session": "S1"}
    request.update(overrides)
    return request


def _act_request(action, text=None, **overrides):
    request = {"operation": "act", "session": "S1", "action": action, "text": text}
    request.update(overrides)
    return request


def _click(node=7, **extra):
    return {"id": "e7", "node": node, "kind": "click", "label": "Continue", "value": "", **extra}


def _evaluate_returning(value, **extra):
    return {"result": {"value": value}, **extra}


def observed(url="https://x/", text="hola", **extra):
    """Un estado como el que devuelve snapshot.js, con todo lo que browser.py después usa."""
    state = {
        "url": url, "text": text, "actions": [], "scroll": 0,
        "page_key": "PK", "guards": {}, "marker": "M1",
    }
    state.update(extra)
    return state


@pytest.fixture
def recorder(monkeypatch):
    def install(answers=None):
        book = Recorder(answers)
        monkeypatch.setattr(cdp_browser, "cdp", book)
        return book

    return install


# ── 1 · observar ───────────────────────────────────────────────────────────────


def test_observing_returns_the_state_with_a_fingerprint(recorder):
    # Una copia nueva en cada llamada: el código añade 'fingerprint' y 'screenshot' al dict
    # que le devuelve el navegador, y un fixture compartido contaminaría la segunda llamada.
    state = observed()
    recorder({"Runtime.evaluate": lambda: _evaluate_returning(dict(state))})
    info = browser_operation(_observe_request(screenshot=False))
    assert info["url"] == "https://x/"
    assert info["fingerprint"] == fingerprint(state)
    assert "screenshot" not in info


def test_a_document_that_is_navigating_is_a_stale_page_and_not_a_crash(recorder):
    """El caso más frecuente de todos: la página se movió justo antes de mirar."""
    recorder({"Runtime.evaluate": _evaluate_returning(None)})
    with pytest.raises(StalePage, match="navigating"):
        browser_operation(_observe_request())


def test_an_exception_inside_the_page_is_a_stale_page(recorder):
    recorder({"Runtime.evaluate": _evaluate_returning(None, exceptionDetails={"text": "boom"})})
    with pytest.raises(StalePage, match="Document changed"):
        browser_operation(_observe_request())


def test_the_screenshot_is_only_asked_for_when_the_caller_wants_it(recorder):
    book = recorder({
        "Runtime.evaluate": lambda: _evaluate_returning(observed()),
        "Page.captureScreenshot": {"data": "BASE64"},
    })
    info = browser_operation(_observe_request(screenshot=True))
    assert info["screenshot"] == "BASE64"
    assert "Page.captureScreenshot" in book.methods()


def test_without_the_screenshot_the_browser_is_never_asked_for_one(recorder):
    """Cada captura son ~30 KB de base64 por decisión:asking sin quererlas cuesta caro."""
    book = recorder({"Runtime.evaluate": lambda: _evaluate_returning(observed())})
    assert "screenshot" not in browser_operation(_observe_request(screenshot=False))
    assert "Page.captureScreenshot" not in book.methods()


# ── 2 · el fingerprint ─────────────────────────────────────────────────────────


def test_the_fingerprint_changes_with_the_page_and_not_with_its_order():
    one = {"url": "https://x/", "text": "a", "actions": [1], "scroll": 0}
    same = {"scroll": 0, "actions": [1], "text": "a", "url": "https://x/"}
    other = dict(one, text="b")
    assert fingerprint(one) == fingerprint(same), "el mismo contenido da el mismo fingerprint"
    assert fingerprint(one) != fingerprint(other)


# ── 3 · el caso central: la expresión que se ejecuta ───────────────────────────


def test_the_action_reaches_the_browser_as_an_object_not_as_a_string():
    """H8. La acción se concatenaba entre comillas y el literal se cerraba en su primera
    comilla interna. Estos tests existían para que eso no pueda volver a pasar en silencio."""
    expression = target_expression(_click())
    assert expression.rstrip().endswith(")"), "la expresión debe cerrar la llamada a la flecha"
    assert not expression.rstrip().endswith('")'), "la acción no puede ir entrecomillada"


@pytest.mark.parametrize("value", ['say "hi"', "línea con acentos: Zürich", "back\\slash", ""])
def test_the_expression_survives_values_that_break_naive_concatenation(value):
    """Un valor con comillas es lo primero que habría destrozado el arreglo anterior."""
    expression = target_expression(_click(value=value))
    assert expression.rstrip().endswith(")")


def test_the_generated_expression_is_valid_javascript(tmp_path):
    """Comprobado por node, no leyendo el concatenado."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")
    path = tmp_path / "target.js"
    # Se sustituye la parte que depende del navegador por un stub mínimo, y se ejecuta: la
    # prueba es que la expresión se pueda evaluar, no que tenga la forma correcta.
    path.write_text(
        "globalThis.window = { __jevFast: { nodes: new Map() }, innerWidth: 1000, innerHeight: 800 };\n"
        "globalThis.document = { elementFromPoint: () => ({}) };\n"
        f"const fn = {target_expression(_click())};\n"
        "process.stdout.write(String(typeof fn));\n",
        encoding="utf-8",
    )
    out = subprocess.run([node, str(path)], capture_output=True, encoding="utf-8", timeout=30, check=True)
    assert out.stdout.strip() == "object", "la expresión debe producir la función, no fallar al analizarse"


def test_a_click_presses_and_releases_at_the_centre_of_the_element(recorder):
    book = recorder({"Runtime.evaluate": _evaluate_returning({"x": 60, "y": 35})})
    result = browser_operation(_act_request(_click()))
    assert result == {"executed": "e7"}
    events = book.params_for("Input.dispatchMouseEvent")
    assert [event["type"] for event in events] == ["mousePressed", "mouseReleased"]
    assert all((event["x"], event["y"]) == (60, 35) for event in events)
    assert "Input.insertText" not in book.methods()


def test_a_fill_selects_the_old_text_before_typing_the_new_one(recorder):
    """Sin select-all, escribir añade al valor que ya había: el login acaba en 'useruser'."""
    book = recorder({"Runtime.evaluate": _evaluate_returning({"x": 10, "y": 20})})
    action = {"id": "e9", "node": 9, "kind": "fill", "label": "User", "value": ""}
    browser_operation(_act_request(action, text="standard_user"))
    keys = book.params_for("Input.dispatchKeyEvent")
    assert [event["type"] for event in keys] == ["keyDown", "keyUp"]
    assert all(event["key"] == "a" for event in keys)
    assert keys[0]["commands"] == ["selectAll"], "sin seleccionar antes, el texto se concatenaría"
    assert book.params_for("Input.insertText")[0]["text"] == "standard_user"


def test_the_select_all_modifier_matches_the_platform(recorder):
    """En macOS es Command (4) y en el resto Control (2): con el modificador equivocado
    'seleccionar todo' no selecciona nada y el texto se concatena."""
    action = {"id": "e9", "node": 9, "kind": "fill", "label": "User", "value": ""}
    book = recorder({"Runtime.evaluate": _evaluate_returning({"x": 1, "y": 1})})
    browser_operation(_act_request(action, text="x"))
    expected = 4 if sys.platform == "darwin" else 2
    assert all(event["modifiers"] == expected for event in book.params_for("Input.dispatchKeyEvent"))
    assert book.params_for("Input.insertText"), "sin insertar texto, rellenar no hace nada"


def test_a_scroll_dispatches_a_wheel_and_never_evaluates_a_node(recorder):
    book = recorder()
    action = {"id": "e1", "node": 1, "kind": "scroll", "label": "abajo", "delta": 620}
    assert browser_operation(_act_request(action)) == {"executed": "e1"}
    wheel = book.params_for("Input.dispatchMouseEvent")[0]
    assert (wheel["type"], wheel["deltaY"]) == ("mouseWheel", 620)
    assert "Runtime.evaluate" not in book.methods(), "un scroll no necesita tocar ningún nodo"


def test_a_select_does_not_click_and_does_not_type(recorder):
    """El desplegable se resuelve asignando e.value en la expresión; hacer clic encima
    abriría el menú nativo y dejaría la misión colgada."""
    book = recorder({"Runtime.evaluate": _evaluate_returning({"x": 5, "y": 6})})
    action = {"id": "e3", "node": 3, "kind": "select", "label": "Pais", "value": "CH"}
    browser_operation(_act_request(action))
    assert "Input.dispatchMouseEvent" not in book.methods()
    assert "Input.insertText" not in book.methods()


def test_a_wait_does_nothing_at_all(recorder):
    book = recorder()
    action = {"id": "e0", "node": 0, "kind": "wait", "label": "esperar", "delta": 0}
    assert browser_operation(_act_request(action)) == {"executed": "e0"}
    assert book.calls == [], "esperar no debería hablar con el navegador"


# ── 4 · lo que no puede llegar al navegador ────────────────────────────────────


@pytest.mark.parametrize("node", ["7", 7.0, None, True, [7]])
def test_a_node_that_is_not_an_integer_never_becomes_a_selector(recorder, node):
    """La promesa del repositorio: el modelo produce un id observado o nada."""
    book = recorder()
    with pytest.raises(ValueError, match="Invalid observed node"):
        browser_operation(_act_request(_click(node=node)))
    assert "Runtime.evaluate" not in book.methods()


def test_a_vanished_target_on_a_click_asks_for_a_fresh_observation(recorder):
    """StalePage se reintenta, y el bucle vuelve a observar. Es reintentable a propósito."""
    recorder({"Runtime.evaluate": _evaluate_returning(None)})
    with pytest.raises(StalePage, match="Target changed or is covered"):
        browser_operation(_act_request(_click()))


def test_a_vanished_target_on_a_select_is_not_retried_automatically(recorder):
    """Un desplegable a medio cambiar puede dejar el menú abierto: reintentarlo a ciegas
    se traduce en teclear en el sitio equivocado. Pide que lo mire alguien."""
    recorder({"Runtime.evaluate": _evaluate_returning(None)})
    with pytest.raises(RuntimeError, match="not confirmed"):
        browser_operation(_act_request({"id": "e3", "node": 3, "kind": "select", "label": "P", "value": "CH"}))


def test_an_exception_during_a_select_is_reported_as_such(recorder):
    recorder({"Runtime.evaluate": _evaluate_returning(None, exceptionDetails={"text": "boom"})})
    with pytest.raises(RuntimeError, match="Dropdown execution was interrupted"):
        browser_operation(_act_request({"id": "e3", "node": 3, "kind": "select", "label": "P", "value": "CH"}))


# ── 5 · la clase Browser, sin daemon ni pestaña ────────────────────────────────


def _browser_with(monkeypatch, book):
    """Un Browser construido a mano: sin daemon, sin pestaña, solo con la sesión."""
    monkeypatch.setattr(cdp_browser, "cdp", book)
    instance = object.__new__(Browser)
    instance.session = "S1"
    instance.target = "T1"
    instance.after_input = None
    return instance


def test_evaluate_returns_the_value_and_turns_an_exception_into_a_stale_page(monkeypatch, recorder):
    book = recorder({"Runtime.evaluate": _evaluate_returning(41)})
    instance = _browser_with(monkeypatch, book)
    assert instance.evaluate("1+1") == 41
    recorder({"Runtime.evaluate": _evaluate_returning(None, exceptionDetails={"text": "x"})})
    with pytest.raises(StalePage, match="Document changed during evaluation"):
        instance.evaluate("boom")


def test_freshness_of_a_click_compares_the_live_guard_with_the_observed_one(monkeypatch, recorder):
    """Un clic necesita el nodo en su sitio: si se movió, la decisión ya no vale."""
    page = {"page_key": "PK", "guards": {"7": "G7"}}
    book = recorder({"Runtime.evaluate": _evaluate_returning(["PK", "G7"])})
    instance = _browser_with(monkeypatch, book)
    assert instance.fresh(page, _click()) is True

    book.answers["Runtime.evaluate"] = _evaluate_returning(["PK", "OTRO"])
    assert instance.fresh(page, _click()) is False


def test_a_non_integer_node_is_stale_without_asking_the_browser(monkeypatch, recorder):
    """Un id que no es un entero no puede estar en el mapa de nodos: no se pregunta."""
    book = recorder()
    instance = _browser_with(monkeypatch, book)
    assert instance.fresh({"page_key": "PK", "guards": {}}, _click(node="7")) is False
    assert "Runtime.evaluate" not in book.methods()


def test_a_scroll_or_a_fill_is_judged_by_the_page_marker(monkeypatch, recorder):
    """Rellenar y desplazarse no tocan un nodo concreto: los juzga el marcador de la página."""
    page = {"marker": "M1"}
    book = recorder({"Runtime.evaluate": _evaluate_returning("M1")})
    instance = _browser_with(monkeypatch, book)
    assert instance.fresh(page) is True
    book.answers["Runtime.evaluate"] = _evaluate_returning("M2")
    assert instance.fresh(page) is False


def test_acting_on_a_page_that_moved_never_touches_the_browser(monkeypatch, recorder):
    """La comprobación es inmediatamente anterior a la mutación: si falla, no se muta nada.

    Es la regla del repositorio de no reintentar una mutación a ciegas, y aquí es un test.
    """
    book = recorder({"Runtime.evaluate": _evaluate_returning(["OTRO", "G7"])})
    instance = _browser_with(monkeypatch, book)
    # aquí solo se llega a la primera evaluación, que es la que falla
    with pytest.raises(StalePage, match="Observe again"):
        instance.act(_click(), observed(marker="M1", guards={"7": "G7"}))
    assert "Input.dispatchMouseEvent" not in book.methods()


def test_acting_records_the_action_for_the_observation_that_follows(monkeypatch, recorder):
    """Tras una entrada hay una espera: un desplegable necesita dos cuadros para abrirse."""
    # Primero la frescura (page key y guard), después el objetivo: dos evaluaciones distintas.
    book = recorder({"Runtime.evaluate": [
        _evaluate_returning(["PK", "G7"]),
        _evaluate_returning({"x": 60, "y": 35}),
    ]})
    instance = _browser_with(monkeypatch, book)
    instance.act(_click(), observed(guards={"7": "G7"}))
    assert instance.after_input is not None, "la acción ejecutada espera antes de la siguiente observación"


def test_closing_closes_the_target_and_never_twice(monkeypatch, recorder):
    book = recorder()
    instance = _browser_with(monkeypatch, book)
    instance.close()
    instance.close()
    assert book.methods() == ["Target.closeTarget"], "cerrar dos veces no puede cerrar dos pestañas"
    assert instance.target is None
