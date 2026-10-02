"""H9 · el plan no puede llevar a un dominio que nadie ha visto.

El hallazgo de la batería de once misiones: el planificador inventó dominios que el usuario
nunca escribió — M1 derivó a AFRINIC, que es el registro regional de la web, no el destino
— y el ejecutor siguió la instrucción. Un plan es una instrucción, y una instrucción que
lleva a un sitio que nadie ha visto convierte al ejecutor en un cursor obediente.

El ancla son las tres cosas que el agente ya vio: el objetivo, la URL de la página y el
texto que hay en pantalla. Un dominio que aparece en la página es descubrible, así que
nombrarlo no es inventarlo — que es lo que hace falta para que «buscar NVIDIA NIM» siga
siendo una misión legítima y no un error del filtro.

Estos tests no miden al planificador, que es una llamada de red. Miden el filtro, que es la
parte que decide, y miden que no rompa las misiones que tienen que funcionar.
"""

import pytest

from jev_ultrafast import model
from jev_ultrafast.model import (
    _planner_request,
    _same_site,
    anchored_steps,
    mentioned_domains,
    plan_domains,
    plan_domains_enabled,
    plan_steps,
    replan_steps,
)

EXAMPLE = {"url": "https://example.com/", "title": "Example Domain", "text": "This domain is for use in examples."}
SEARCH = {
    "url": "https://lite.duckduckgo.com/lite/",
    "title": "DuckDuckGo",
    "text": "https://docs.nvidia.com/nim    https://build.nvidia.com",
}


# ── 1 · qué cuenta como dominio, y qué no ───────────────────────────────────────


def test_a_domain_written_in_the_goal_is_read():
    assert "iana.org" in mentioned_domains("Pulsar el enlace y llegar a iana.org")


def test_a_domain_in_a_url_is_read_without_the_scheme():
    assert mentioned_domains("ver https://nvidia.com/docs") == {"nvidia.com"}


def test_domains_are_lowercased_so_the_comparison_is_not_case_sensitive():
    assert mentioned_domains("NVIDIA.COM y WW.Example.COM") == {"nvidia.com", "ww.example.com"}


def test_a_file_name_is_not_a_domain():
    """Sin una lista de TLDs, «abrir config.js» sería un dominio y su paso se eliminaría.

    Es el falso positivo que importa: la misión más legítima del mundo —abrir un archivo—
    no debe perder un paso por una expresión regular demasiado credula.
    """
    assert mentioned_domains("abrir config.js y luego styles.css") == set()


def test_an_unknown_suffix_is_not_treated_as_a_domain():
    """Lo que no se reconoce no se restringe: la equivocación va siempre hacia dejar pasar."""
    assert mentioned_domains("el fichero informe2.zzz y el paquete thing.qqq") == set()


def test_no_text_means_no_domains_and_does_not_raise():
    assert mentioned_domains(None, "", None) == set()
    assert mentioned_domains() == set()


# ── 2 · dos dominios son el mismo sitio o no lo son ─────────────────────────────


def test_a_subdomain_is_the_same_site_as_its_parent():
    assert _same_site("docs.nvidia.com", "nvidia.com")
    assert _same_site("www.iana.org", "iana.org")


def test_two_unrelated_domains_are_not_the_same_site():
    assert not _same_site("afrinic.net", "example.com")


def test_a_domain_that_only_ends_with_the_same_letters_is_not_the_same_site():
    """'notexample.com' no es 'example.com', aunque acabe igual. Una etiqueta es una etiqueta."""
    assert not _same_site("notexample.com", "example.com")


# ── 3 · el ancla: objetivo, URL de página y texto de página ─────────────────────


def test_the_anchor_is_the_goal_and_the_current_page():
    assert plan_domains("llegar a iana.org", EXAMPLE) == {"iana.org", "example.com"}


def test_a_domain_visible_on_the_page_is_anchored():
    """Es lo que hace que M2 siga siendo una misión legítima."""
    anchored = plan_domains("Buscar NVIDIA NIM y abrir el primer resultado", SEARCH)
    assert "docs.nvidia.com" in anchored
    assert "lite.duckduckgo.com" in anchored


def test_a_page_without_anything_gives_an_empty_anchor():
    assert plan_domains("hacer algo", {}) == set()
    assert plan_domains("hacer algo", None) == set()


# ── 4 · el filtro ───────────────────────────────────────────────────────────────


def test_a_step_to_an_invented_domain_is_dropped():
    allowed = plan_domains("llegar a iana.org", EXAMPLE)
    steps = [
        "Pulsar el enlace More information",
        "Ir a afrinic.net para registrar el dominio",
        "Confirmar que la página es iana.org",
    ]
    assert anchored_steps(steps, allowed) == [
        "Pulsar el enlace More information",
        "Confirmar que la página es iana.org",
    ]


def test_the_steps_already_known_all_survive():
    allowed = plan_domains("llegar a iana.org", EXAMPLE)
    steps = ["Pulsar More information", "Comprobar iana.org en la barra"]
    assert anchored_steps(steps, allowed) == steps


def test_a_plan_entirely_invented_comes_back_empty_and_the_caller_falls_back():
    """Vacío es la señal que ya entienden los dos llamadores: `if steps:` y `if not steps: return False`.

    Es el comportamiento seguro: la misión se ejecuta con el objetivo único, que es como
    funcionaba el agente sin planificador, en vez de con un plan lleno de sitios inventados.
    """
    allowed = plan_domains("llegar a iana.org", EXAMPLE)
    assert anchored_steps(["Ir a afrinic.net", "Consultar whois.com por si acaso"], allowed) == []


def test_a_domain_with_an_unlisted_suffix_slips_through_on_purpose():
    """Lo que el filtro no reconoce, lo deja pasar. Se escribe aquí para que no se olvide.

    La lista de TLDs es corta a propósito, y el error va siempre hacia dejar pasar el paso:
    un falso positivo borraría el paso de una misión legítima —abrir la documentación que
    está en pantalla— mientras que un falso negativo solo deja pasar un paso improbable. Los
    dominios que un planificador alucina son sitios de verdad, con TLD común; uno inventado
    con un sufijo raro tampoco resolvería.
    """
    assert mentioned_domains("Ir a who.is") == set()
    allowed = plan_domains("llegar a iana.org", EXAMPLE)
    assert anchored_steps(["Ir a who.is"], allowed) == ["Ir a who.is"]


def test_a_step_with_no_domain_is_never_touched():
    """La mayoría de los pasos son verbos, no direcciones: 'Pulsar el botón' no es una URL."""
    allowed = plan_domains("llegar a iana.org", EXAMPLE)
    steps = ["Pulsar el botón azul", "Esperar a que cargue", "Leer el texto"]
    assert anchored_steps(steps, allowed) == steps


def test_anchoring_twice_is_the_same_as_once():
    allowed = plan_domains("llegar a iana.org", EXAMPLE)
    once = anchored_steps(["Pulsar", "Ir a afrinic.net"], allowed)
    assert anchored_steps(once, allowed) == once


def test_an_empty_anchor_does_not_filter_anything():
    """Sin nada a la vista no hay contra qué anclar, y filtrar sería romper la misión por nada.

    Es el caso de la página local del fixture, donde el objetivo y la URL no nombran dominio.
    """
    steps = ["Pulsar el enlace de la casa", "Reservar"]
    assert anchored_steps(steps, set()) == steps


# ── 5 · la válvula de seguridad ─────────────────────────────────────────────────


def test_the_filter_is_on_by_default_without_configuring_anything():
    assert plan_domains_enabled() is True


@pytest.mark.parametrize("value", ["off", "0", "false", "no", "OFF", " Off "])
def test_it_can_be_turned_off(monkeypatch, value):
    monkeypatch.setenv("JEV_PLAN_DOMAINS", value)
    assert plan_domains_enabled() is False
    assert anchored_steps(["Ir a afrinic.net"], {"example.com"}) == ["Ir a afrinic.net"]


def test_anything_else_leaves_it_on(monkeypatch):
    monkeypatch.setenv("JEV_PLAN_DOMAINS", "on")
    assert plan_domains_enabled() is True


# ── 6 · el planificador se entera de la regla antes de producirla ───────────────


def test_the_request_carries_the_domains_already_in_view():
    user = _planner_request("llegar a iana.org", EXAMPLE, allowed={"iana.org", "example.com"})
    assert "iana.org" in user
    assert "DOMAINS ALREADY IN VIEW" in user
    assert "Do not name or navigate to any other domain" in user


def test_the_request_says_nothing_when_there_is_nothing_to_anchor():
    user = _planner_request("hacer algo", EXAMPLE, allowed=set())
    assert "DOMAINS ALREADY IN VIEW" not in user


def test_the_request_stays_quiet_when_the_filter_is_off(monkeypatch):
    monkeypatch.setenv("JEV_PLAN_DOMAINS", "off")
    user = _planner_request("llegar a iana.org", EXAMPLE, allowed={"iana.org"})
    assert "DOMAINS ALREADY IN VIEW" not in user


def test_the_list_is_capped_so_a_long_page_cannot_blow_up_the_prompt():
    """Una página con cien enlaces no puede convertir el prompt del planificador en otro prompt."""
    many = {f"site{i}.example.com" for i in range(40)}
    user = _planner_request("x", EXAMPLE, allowed=many)
    listed = user.split("DOMAINS ALREADY IN VIEW:")[1].splitlines()[0]
    assert listed.count("site") <= 12, listed


# ── 7 · de extremo a extremo, con un planificador de mentira ────────────────────


class _Planner:
    """Responde con una lista fija: el filtro es lo que se está probando."""

    def __init__(self, steps):
        self.steps = steps
        self.calls = []

    def __call__(self, *args, **kwargs):
        raise AssertionError("el planificador no debe llamarse aquí")


def _patch_planner(monkeypatch, steps):
    from jev_ultrafast import providers

    provider = {"name": "fake", "model": "fake-model", "key": "fake"}

    def chat(_provider, _system, user, **kwargs):
        return __import__("json").dumps({"steps": steps}), {}

    monkeypatch.setattr(providers, "resolve", lambda role: provider)
    monkeypatch.setattr(providers, "chat", chat)


def test_a_plan_with_one_invented_domain_loses_exactly_that_step(monkeypatch):
    _patch_planner(monkeypatch, ["Pulsar More information", "Ir a afrinic.net", "Comprobar iana.org"])
    assert plan_steps("Pulsar y llegar a iana.org", EXAMPLE) == [
        "Pulsar More information", "Comprobar iana.org",
    ]


def test_the_real_m2_mission_survives_the_filter(monkeypatch):
    """La batería de antes hizo esta misión. Romperla sería cambiar un fallo por otro."""
    _patch_planner(monkeypatch, [
        "Escribir NVIDIA NIM en el buscador",
        "Abrir el primer resultado",
        "Ir a nvidia.com para ver la documentación",
    ])
    plan = plan_steps("Buscar NVIDIA NIM y abrir el primer resultado", SEARCH)
    assert plan == [
        "Escribir NVIDIA NIM en el buscador",
        "Abrir el primer resultado",
        "Ir a nvidia.com para ver la documentación",
    ]


def test_replanning_is_anchored_too(monkeypatch):
    """Un replan es un plan nuevo, y por lo mismo puede volver a alucinar un dominio."""
    _patch_planner(monkeypatch, ["Reintentar el enlace", "Ir a afrinic.net"])
    steps = replan_steps(
        "Pulsar y llegar a iana.org", ["Pulsar More information"], 0,
        "tres acciones no cambiaron nada", EXAMPLE, [],
    )
    assert steps == ["Reintentar el enlace"]


def test_a_fully_invented_replan_returns_empty_so_the_agent_keeps_its_plan(monkeypatch):
    """`if not steps: return False` en agent.py: el plan anterior se conserva."""
    _patch_planner(monkeypatch, ["Ir a afrinic.net"])
    assert replan_steps("x", ["a"], 0, "razón", EXAMPLE, []) == []


def test_the_module_level_tld_list_has_no_surprises():
    """Guardas estructurales: la lista decide qué se restringe, así que no puede crecer a lo bruto."""
    assert model.KNOWN_TLDS >= {"com", "net", "org", "es", "io", "dev", "uk", "de"}
    assert "js" not in model.KNOWN_TLDS
    assert all(tld.islower() and tld.isalpha() for tld in model.KNOWN_TLDS)
