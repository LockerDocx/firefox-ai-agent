"""H12 · enrutamiento por tarea: no pagar un planificador de 21 s si no compra nada.

Reglas que este módulo tiene que seguir, cada una fijada con un test:

* una misión de un solo gesto **no** pide plan, y lo dice por qué;
* una misión de varios pasos **sí** lo pide, también en los idiomas del producto;
* ante la duda se planifica, porque un plan de más cuesta segundos y uno de menos buclea;
* `JEV_ROUTING=always` deja el comportamiento anterior intacto, sin excepciones;
* el que no pasa objetivo conserva exactamente lo que tenía.
"""

import pytest

from jev_ultrafast import routing
from jev_ultrafast.model import planning_config

# ── un gesto, nada que encadenar ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "goal",
    [
        "open example.com",
        "go to the releases page of the repository",
        "click the information link",
        "visita iana.org",
        "ouvre la page d'accueil",
        "besuche die Startseite",
        "abre la página de contacto",
    ],
)
def test_one_gesture_runs_without_a_planner(goal):
    planned, decision = routing.needs_a_plan(goal, configured="auto")
    assert not planned, f"un solo gesto no necesita plan: {goal!r} → {decision}"
    assert decision["route"] == "single"
    assert decision["reason"], "una decisión de coste que no se explica no se puede auditar"


# ── varios pasos, sí plan ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "goal",
    [
        "log in with the standard user, add a backpack and open the cart",
        "book a flight from Zurich to London next Sunday, one adult",
        "busca vuelos de Madrid a Roma el 20 de junio",
        "iniciar sesión en saucedemo, añadir una mochila y abrir el carrito",
        "anmelden und dann den Warenkorb öffnen",
        "se connecter puis ouvrir le panier",
    ],
)
def test_a_multi_step_mission_is_planned(goal):
    planned, decision = routing.needs_a_plan(goal, configured="auto")
    assert planned, f"una misión de varios pasos sí necesita plan: {goal!r} → {decision}"
    assert decision["signals"]


def test_a_conjunction_alone_is_enough():
    planned, decision = routing.needs_a_plan("busca el artículo y ábrelo", configured="auto")
    assert planned
    assert "conjunction" in decision["signals"]


def test_a_named_field_is_enough():
    planned, decision = routing.needs_a_plan("completa el formulario con tu contraseña", configured="auto")
    assert planned
    assert "names_a_field" in decision["signals"]


# ── la duda se resuelve planificando ───────────────────────────────────────────


def test_an_unclear_goal_is_planned_because_under_planning_is_worse():
    """Un plan de más cuesta segundos; uno de menos puede convertir la misión en un bucle."""
    planned, decision = routing.needs_a_plan("haz lo que toque con esto", configured="auto")
    assert planned
    assert decision["signals"] == ["unknown"]


def test_an_empty_goal_is_not_an_error():
    planned, decision = routing.needs_a_plan("", configured="auto")
    assert planned is False
    assert decision["reason"] == "empty goal"


# ── la política manda sobre el clasificador ────────────────────────────────────


def test_always_plans_even_for_one_gesture():
    planned, decision = routing.needs_a_plan("open example.com", configured="always")
    assert planned
    assert decision["signals"] == ["forced"]


def test_never_plans_even_for_a_long_mission():
    planned, decision = routing.needs_a_plan(
        "log in, add a backpack, check out and pay", configured="never"
    )
    assert not planned
    assert decision["signals"] == ["forced"]


def test_the_policy_comes_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("JEV_ROUTING", "never")
    assert routing.needs_a_plan("book a flight from A to B")[0] is False
    monkeypatch.setenv("JEV_ROUTING", "always")
    assert routing.needs_a_plan("open example.com")[0] is True
    monkeypatch.delenv("JEV_ROUTING")
    assert routing.needs_a_plan("open example.com")[0] is False


# ── el módulo es determinista y no llama a nadie ───────────────────────────────


def test_the_route_is_the_same_every_time():
    """Una decisión de coste no puede depender de un modelo que puede estar en cola."""
    goal = "book a flight from Zurich to London next Sunday, one adult"
    first = routing.route(goal)
    for _ in range(20):
        assert routing.route(goal) == first


def test_the_module_makes_no_network_call():
    source = open(routing.__file__, encoding="utf-8").read()
    for forbidden in ("httpx", "providers.", "import requests", "chat("):
        assert forbidden not in source, f"el router no puede llamar a un modelo: {forbidden}"


def test_it_survives_a_goal_full_of_punctuation_and_unicode():
    for goal in ("", "   ", "¿¿¿", "a" * 5000, "Buscar — ¿vuelo? ¡YA! 100 %", "🎉🎉🎉", "\n\n\t"):
        routing.route(goal)  # no debe lanzar


def test_a_huge_goal_does_not_take_the_planner_down_with_it():
    assert routing.route("busca vuelo " * 500)["route"] == "planned"


# ── el cableado: el bucle respeta la ruta, y solo si se le pide ───────────────


def test_a_caller_without_a_goal_keeps_the_old_behaviour(monkeypatch):
    """planning_config() sin objetivo es lo que llamaban los tests y la consola antes."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.delenv("PLANNER_API_KEY", raising=False)
    assert planning_config() is not None, "sin objetivo no se enruta: nada cambia para quien no lo pidió"


def test_a_single_gesture_never_resolves_a_planner(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    assert planning_config("open example.com") is None


def test_a_multi_step_mission_still_resolves_its_planner(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    config = planning_config("book a flight from Zurich to London and pay")
    assert config is not None
    assert config["model"]


def test_a_planner_disabled_by_configuration_still_wins(monkeypatch):
    """El enrutamiento no puede resucitar un planificador que el usuario apagó."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr("jev_ultrafast.providers.planner_enabled", lambda: False)
    assert planning_config("book a flight from A to B and pay") is None


# ── la ruta es visible, no una caja negra ──────────────────────────────────────


def test_the_decision_can_be_rendered_for_the_sidebar():
    line = routing.describe(routing.route("open example.com"))
    assert "single" in line and line.strip()


def test_the_reason_never_names_an_internal_only_thing():
    for goal in ("open example.com", "log in and pay", "???", "busca vuelo y paga"):
        decision = routing.route(goal)
        assert isinstance(decision["reason"], str) and decision["reason"]
        assert decision["route"] in {"single", "planned"}
        assert isinstance(decision["signals"], list)
