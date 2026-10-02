"""H7 y H4 · el demo tenía 0 % de cobertura y `jev --help` no salía nunca.

Dos cosas en el mismo fichero, porque son el mismo agujero visto desde dos lados.

H7: `jev --help` no imprimía nada y el proceso se quedaba vivo hasta que el timeout lo
mataba. No era un fallo de argparse —es que no había argparse—: `main()` levantaba el
servidor antes de mirar los argumentos, así que `--help` arrancaba el demo, servía en el
8766 y esperaba forever. Un usuario que escribía `jev --help` para ver las opciones se
encontraba con un proceso colgado y un puerto ocupado.

H4: `demo.py` tenía 105 sentencias y cero pruebas. Es el punto de entrada que ejecuta una
gente cuando quiere ver el agente sin abrir Firefox, y ninguna prueba lo tocaba. Estas no
lo cubren entero —levanta un servidor de verdad— pero sí lo que se puede levantar sin
navegador, sin clave y sin red, que es donde estuvo el fallo.

Nada aquí llama a un proveedor ni abre un navegador.
"""

import json
from pathlib import Path

import pytest

from jev_ultrafast import demo

ROOT = Path(__file__).resolve().parent.parent


# ── 1 · H7 · --help sale, y sale antes de levantar nada ─────────────────────────


def test_help_exits_zero_instead_of_hanging(capsys):
    """El síntoma medido: nada impreso, proceso vivo, código 124 al matarlo."""
    with pytest.raises(SystemExit) as exit_info:
        demo.parse_arguments(["--help"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert "usage: jev" in printed
    assert "--port" in printed and "--scenario" in printed


def test_an_unknown_flag_is_refused_rather_than_ignored():
    """Un `--porrt` mal escrito tiene que decir que no existe, no arrancar un servidor en 8766."""
    with pytest.raises(SystemExit) as exit_info:
        demo.parse_arguments(["--porrt", "9000"])
    assert exit_info.value.code == 2


def test_an_unknown_scenario_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        demo.parse_arguments(["--scenario", "teletransporte"])
    assert exit_info.value.code == 2


def test_version_is_reported():
    with pytest.raises(SystemExit) as exit_info:
        demo.parse_arguments(["--version"])
    assert exit_info.value.code == 0
    assert demo.version()


# ── 2 · los argumentos por defecto no cambian lo que ya funcionaba ──────────────


def test_with_no_arguments_it_still_serves_the_default_port():
    args = demo.parse_arguments([])
    assert args.port == demo.PORT
    assert args.scenario == "flights"
    assert args.no_open is False


def test_the_port_and_the_scenario_are_readable():
    args = demo.parse_arguments(["--port", "9123", "--scenario", "travel", "--no-open"])
    assert args.port == 9123
    assert args.scenario == "travel"
    assert args.no_open is True


def test_the_default_port_comes_from_the_environment(monkeypatch):
    """TYPESAFE_DEMO_PORT es lo que ya se podía usar; el flag no puede pisarlo por sorpresa."""
    monkeypatch.setattr(demo, "PORT", 9999)
    assert demo.parse_arguments([]).port == 9999


def test_a_port_that_is_not_a_number_is_refused():
    with pytest.raises(SystemExit) as exit_info:
        demo.parse_arguments(["--port", "mil"])
    assert exit_info.value.code == 2


def test_the_three_scenarios_are_the_ones_the_reset_command_accepts():
    """`--scenario` y el `scenario` del POST tienen que admitir exactamente lo mismo.

    Si uno acepta un valor que el otro no, el panel abre una página que el servidor rechaza.
    """
    accepted = set(demo.SCENARIOS)
    for scenario in accepted:
        assert scenario in {"travel", "research", "flights"}


# ── 3 · el estado que ve el panel ──────────────────────────────────────────────


def _state_without_agent():
    return demo.response_state()


def test_the_panel_gets_a_usable_state_before_any_mission_starts():
    """Lo primero que hace el panel es leer /api/state, y no puede recibir un None."""
    state = _state_without_agent()
    assert state["status"] == "idle"
    assert state["history"] == []
    assert state["page"] is None
    assert state["decision"] is None
    assert state["max_steps"] > 0
    assert state["policy_model"]


def test_the_state_serialises_as_json():
    """El handler lo pasa por json.dumps; un valor que no serialice rompe el panel entero."""
    json.dumps(_state_without_agent())


def test_a_command_before_any_demo_is_refused():
    """`tick` sin misión no puede ser un silencio: sería un bucle que no avanza."""
    with pytest.raises(ValueError, match="Start a demo first"):
        demo.command("tick", {})


# ── 4 · el objetivo se valida antes de gastar un navegador ─────────────────────


class _NeverBuilt:
    """Si el objetivo no pasa la validación, no debe construirse ningún agente."""

    def __init__(self):
        raise AssertionError("se construyó un agente para un objetivo inválido")


def _no_agent(monkeypatch):

    monkeypatch.setattr(demo, "Agent", _NeverBuilt)


@pytest.mark.parametrize("goal", ["", "   ", "x" * 2001])
def test_an_empty_or_absurd_goal_never_reaches_the_browser(monkeypatch, goal):
    _no_agent(monkeypatch)
    with pytest.raises(ValueError):
        demo.command("reset", {"goal": goal, "scenario": "flights"})


def test_a_missing_goal_is_refused(monkeypatch):
    _no_agent(monkeypatch)
    with pytest.raises(ValueError):
        demo.command("reset", {"scenario": "flights"})


def test_an_unknown_scenario_is_refused_before_the_browser(monkeypatch):
    _no_agent(monkeypatch)
    with pytest.raises(ValueError, match="Unknown demo scenario"):
        demo.command("reset", {"goal": "buscar algo", "scenario": "mars"})


def test_the_loopback_only_rule_of_the_handler_is_still_written_down():
    """El demo es un inspector local y sin credenciales; atarse a 127.0.0.1 es la condición."""
    assert 'if self.headers.get("Host") != f"127.0.0.1:{PORT}"' in (ROOT / "jev_ultrafast" / "demo.py").read_text(
        encoding="utf-8"
    )


def test_the_post_needs_the_shared_token():
    """Un POST sin el token es un proceso local cualquiera, no el panel."""
    source = (ROOT / "jev_ultrafast" / "demo.py").read_text(encoding="utf-8")
    assert 'self.headers.get("X-Demo-Token") != TOKEN' in source
    assert "Local demo requests only" in source


# ── 5 · el fichero es ejecutable y su entrada es la del comando ────────────────


def test_the_jev_command_still_points_at_this_main():
    """Si `jev` dejara de apuntar aquí, --help arreglado no arreglaría el comando."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'jev = "jev_ultrafast.demo:main"' in pyproject


def test_main_reads_its_arguments_before_it_touches_the_network(monkeypatch):
    """El orden es el arreglo: si main() pidiera la clave antes de parsear, --help colgaría
    en una máquina sin clave, que es exactamente la máquina donde se escribe --help."""
    order = []

    class _Args:
        port = 9200
        scenario = "flights"
        no_open = True

    monkeypatch.setattr(demo, "parse_arguments", lambda argv=None: (order.append("parse"), _Args())[1])
    monkeypatch.setattr(demo, "load_environment", lambda: order.append("env"))

    from jev_ultrafast import providers

    def refuse(*_args, **_kwargs):
        order.append("key")
        return True

    monkeypatch.setattr(providers, "ensure_configured", refuse)

    class _Server:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("stop: reached the server")

    monkeypatch.setattr(demo, "ThreadingHTTPServer", _Server)
    with pytest.raises(RuntimeError, match="reached the server"):
        demo.main(["--port", "9200", "--no-open"])
    assert order == ["parse", "env", "key"], order


# ── 6 · la garantía de loopback, que vive en el handler ────────────────────────


class _Headers:
    def __init__(self, **values):
        self._values = {key.replace("_", "-"): value for key, value in values.items()}

    def get(self, key, default=None):
        return self._values.get(key, default)


class _Socket:
    """Lo único que el handler necesita de un socket: escribir y cerrar."""

    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data

    def close(self):
        pass


def _handler(monkeypatch, **headers):
    """Un Handler sin servidor: se construye a mano y se le dan sus dos entradas."""
    instance = object.__new__(demo.Handler)
    instance.headers = _Headers(**headers)
    instance.wfile = _Socket()
    instance.request_version = "HTTP/1.1"
    instance.command = "GET"
    instance.path = "/api/state"
    instance.responses = []
    monkeypatch.setattr(instance, "send_response", lambda code: instance.responses.append(code), raising=False)
    return instance


def test_a_get_from_another_host_is_refused(monkeypatch):
    """El demo se ata a 127.0.0.1, pero el Host también se comprueba: sin eso, un DNS
    rebinding en un navegador que ya está en la máquina lo alcanzaría."""
    instance = _handler(monkeypatch, Host="attacker.example", X_Demo_Token=demo.TOKEN)
    instance.do_GET()
    assert instance.responses == [403]
    # El handler vuelca también las cabeceras en el socket, así que se mira el cuerpo, no
    # el principio del buffer: comparar desde el índice 0 medía el cabecero, no la respuesta.
    assert b"Forbidden" in instance.wfile.written


def test_a_get_with_the_right_host_is_served(monkeypatch):
    instance = _handler(monkeypatch, Host=f"127.0.0.1:{demo.PORT}")
    instance.do_GET()
    assert instance.responses == [200]
    assert b'"status"' in instance.wfile.written, "el panel necesita al menos el estado"


def test_a_post_without_the_token_is_refused(monkeypatch):
    """Cualquier proceso local puede hablar con el puerto: el token es lo que lo distingue."""
    instance = _handler(monkeypatch, Host=f"127.0.0.1:{demo.PORT}", Origin=None)
    instance.request_version = "HTTP/1.1"
    instance.path = "/api/reset"
    instance.do_POST()
    assert instance.responses == [403]
    assert b"Local demo requests only" in instance.wfile.written


def test_a_post_with_the_token_but_from_another_origin_is_refused(monkeypatch):
    """Firefox manda su Origin; si no es el del demo, el POST no es del panel."""
    instance = _handler(monkeypatch, Host=f"127.0.0.1:{demo.PORT}", X_Demo_Token=demo.TOKEN,
                        Origin="https://attacker.example")
    instance.path = "/api/reset"
    instance.do_POST()
    assert instance.responses == [403]


def test_an_oversized_post_is_refused_without_being_read(monkeypatch):
    """El límite se comprueba antes de leer: leer primero es leer lo que mande quien sea."""
    instance = _handler(monkeypatch, Host=f"127.0.0.1:{demo.PORT}", X_Demo_Token=demo.TOKEN,
                        Content_Length="999999")
    instance.path = "/api/reset"
    instance.do_POST()
    assert instance.responses == [400]
    assert b"Invalid request size" in instance.wfile.written


def test_a_failed_command_is_a_400_with_a_reason_and_not_a_500(monkeypatch):
    """Un objetivo vacío es un error del que se puede reintentar, no una excepción del panel."""
    instance = _handler(monkeypatch, Host=f"127.0.0.1:{demo.PORT}", X_Demo_Token=demo.TOKEN,
                        Content_Length=str(len(b'{"goal": ""}')))
    instance.path = "/api/reset"
    instance.rfile = type("R", (), {"read": lambda self, n: b'{"goal": ""}'})()
    instance.do_POST()
    assert instance.responses == [400]
    assert b"goal" in instance.wfile.written or b"characters" in instance.wfile.written
