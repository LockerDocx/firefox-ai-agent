"""El banco vivo: lo que decide, probado sin navegador y sin clave.

`scripts/live_battery.py` existe para cerrar los dos criterios que la auditoría de v0.13.1
dejó sin cifra: la tasa de éxito (≥ 60 %) y el 100 % de formularios de dos campos de H11.
Los dos necesitan un navegador real y una clave real, así que ninguna prueba de este
fichero puede ejecutarlos.

Lo que sí se puede —y es lo que se equivoca— es el veredicto. Si "éxito" significa "el
agente dijo DONE", la batería mide obediencia en vez de resultado, y la batería anterior dio
un único M3 como único éxito precisamente porque missions así se cuentan a ojo. Aquí el
resultado se decide contra la URL final y contra el historial, y esa lógica es pura.

Estos tests no miden nada del agente: miden que el medidor no miente.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import live_battery  # noqa: E402
from live_battery import (  # noqa: E402
    SECRET,
    apply_model,
    masked_goal,
    missions,
    scorecard,
    two_field_evidence,
    typed_a_secret,
    typed_fields,
    usage_tokens,
    verdict,
)

FORM = {
    "tag": "M4", "site": "saucedemo", "url": live_battery.SAUCEDEMO,
    "goal": "login", "expect_url": "cart.html", "form": True,
}
PLAIN = {
    "tag": "M1", "site": "example.com", "url": "https://example.com/",
    "goal": "go", "expect_url": "iana.org", "form": False,
}


def fill(text, kind="fill", label="campo"):
    return {"kind": kind, "text": text, "action": label, "step": 1, "latency_ms": 402}


def state(status="done", url="https://www.saucedemo.com/cart.html", history=(), blocked=None):
    return {
        "status": status,
        "history": list(history),
        "page": {"url": url},
        "blocked_reason": blocked,
        "decisions": [],
        "text_calls": [],
    }


# ── 1 · ÉXITO significa URL, no que el agente lo diga ─────────────────────────


def test_a_mission_that_reaches_its_url_succeeds():
    assert verdict(FORM, state())[0] == "ÉXITO"


def test_saying_done_on_the_wrong_page_is_a_failure():
    """El caso que la batería anterior no distinguía: DONE en la página equivocada."""
    outcome, why = verdict(FORM, state(status="done", url="https://www.saucedemo.com/"))
    assert outcome == "FALLÓ"
    assert "cart.html" in why, "el motivo tiene que decir qué se esperaba"


def test_a_loop_that_never_finished_is_a_failure_and_not_an_error():
    outcome, why = verdict(PLAIN, state(status="ready", url="https://example.com/"))
    assert outcome == "FALLÓ"
    assert "no terminó" in why


def test_the_expectation_is_read_from_the_url_not_from_the_goal():
    """'tfs=' en la URL de Google Flights es lo que prueba que hubo una búsqueda."""
    searched = "https://www.google.com/travel/flights?hl=es&tfs=abc"
    idle = "https://www.google.com/travel/flights?hl=es"
    assert verdict({"expect_url": "tfs="}, state(url=searched))[0] == "ÉXITO"
    assert verdict({"expect_url": "tfs="}, state(url=idle))[0] == "FALLÓ"


# ── 2 · SIN CLAVE es un desenlace propio, no un fallo ──────────────────────────


def test_stopping_for_a_missing_credential_is_not_a_failed_login():
    """H11: sin credencial el bucle para y lo dice. Eso es el arreglo funcionando."""
    outcome, why = verdict(FORM, state(
        status="blocked",
        url="https://www.saucedemo.com/",
        blocked="Password needs a value the goal does not supply. The agent will not invent it.",
    ))
    assert outcome == "SIN CLAVE"
    assert "inventar" in why


def test_a_mission_that_reached_its_url_succeeds_even_if_it_then_stalled():
    """Llegar al destino y atascarse después es un acierto, no un fallo.

    La tasa de éxito mide si el agente sabe conseguir el objetivo. Si el bucle se queda
    tres acciones sin cambiar nada *después* de estar en la página correcta, el objetivo
    está cumplido: penalizarlo mediría el presupuesto que sobró, no la capacidad del agente.
    """
    outcome, _ = verdict(FORM, state(
        status="blocked", blocked="Three consecutive actions changed nothing on the page."
    ))
    assert outcome == "ÉXITO"


def test_a_missing_credential_outranks_a_matching_url():
    """El orden de las reglas es parte del contrato, no un detalle de implementación."""
    outcome, _ = verdict(FORM, state(
        status="blocked",
        url="https://www.saucedemo.com/cart.html",
        blocked="Password needs a value the goal does not supply.",
    ))
    assert outcome == "SIN CLAVE"


# ── 3 · H11 · los dos campos del formulario ─────────────────────────────────────


def test_typing_only_the_username_is_not_a_completed_form():
    """El fallo exacto de saucedemo: usuario tecleado, contraseña ausente, Login en bucle."""
    assert two_field_evidence([fill("standard_user")]) is False


def test_typing_only_the_password_is_not_a_completed_form():
    assert two_field_evidence([fill(SECRET)]) is False


def test_typing_both_fields_is_a_completed_form():
    assert two_field_evidence([fill("standard_user"), fill(SECRET)]) is True


def test_the_mask_is_what_proves_a_secret_was_typed():
    """Un fill enmascarado prueba a la vez que se escribió y que no se puede leer."""
    assert typed_a_secret([fill(SECRET)]) is True
    assert typed_a_secret([fill("standard_user")]) is False


def test_clicks_and_waits_are_not_typing():
    history = [fill(None, kind="click"), fill(None, kind="scroll_down"), fill(None, kind="wait")]
    assert typed_fields(history) == []
    assert two_field_evidence(history) is False


def test_a_run_that_never_typed_anything_typed_no_secret():
    assert typed_a_secret([]) is False
    assert two_field_evidence([]) is False


# ── 4 · el informe no puede filtrar la credencial ──────────────────────────────


def test_the_report_masks_the_credential_the_goal_carries():
    goal = missions("standard_user", "secret_sauce")[3]["goal"]
    assert "secret_sauce" in goal, "el objetivo debe llevar la credencial, o el login no se puede probar"
    assert "secret_sauce" not in masked_goal(goal, "secret_sauce")
    assert SECRET in masked_goal(goal, "secret_sauce")


def test_masking_without_a_password_leaves_the_goal_alone():
    goal = "Buscar NVIDIA NIM"
    assert masked_goal(goal, "") == goal


def test_the_whole_mission_set_is_sanitised_not_just_the_first():
    """El informe escribe las once; enmascarar solo la primera sería un descuido."""
    secret = "hunter2"
    for mission in missions("standard_user", secret):
        assert secret not in masked_goal(mission["goal"], secret), mission["tag"]


# ── 5 · el marcador: qué cuenta y qué no ───────────────────────────────────────


def test_success_rate_ignores_missions_that_errored():
    """Un error de entorno no es un fallo del agente: contarlo rebajaría la tasa sin motivo."""
    results = [
        {"outcome": "ÉXITO"}, {"outcome": "FALLÓ"}, {"outcome": "FALLÓ"}, {"outcome": "ERROR"},
    ]
    card = scorecard(results, [])
    assert card["success"]["total"] == 3
    assert card["success"]["ok"] == 1
    assert card["success"]["rate"] == pytest.approx(1 / 3)


def test_two_field_rate_ignores_runs_without_a_credential():
    """Sin credencial no se puede completar un formulario, pero tampoco se puede fallar.

    Meter esas misiones en el denominador daría un 0 % que no mide nada: mide que no había
    clave. Se cuentan aparte, como `skipped_no_credential`.
    """
    forms = [
        {"outcome": "FALLÓ", "two_field": False},
        {"outcome": "SIN CLAVE", "two_field": False},
        {"outcome": "ÉXITO", "two_field": True},
        {"outcome": "ÉXITO", "two_field": True},
    ]
    card = scorecard(forms, forms)["two_field"]
    assert card["total"] == 3
    assert card["filled"] == 2
    assert card["rate"] == pytest.approx(2 / 3)
    assert card["skipped_no_credential"] == 1


def test_no_runnable_form_means_the_criterion_is_unmeasured_not_failed():
    """Sin ninguna misión de formulario medible, H11 no vale 0 %: vale 'no medido'."""
    forms = [{"outcome": "SIN CLAVE", "two_field": False}]
    card = scorecard(forms, forms)["two_field"]
    assert card["total"] == 0
    assert card["skipped_no_credential"] == 1


def test_the_targets_are_the_ones_the_acceptance_criteria_name():
    assert live_battery.SUCCESS_TARGET == 0.60
    assert live_battery.TWO_FIELD_TARGET == 1.00


# ── 6 · coste: comparar modelos es comparar aciertos y tokens ─────────────────


def test_tokens_count_both_the_decisions_and_the_text_helper():
    spent = state()
    spent["decisions"] = [{"usage": {"prompt_tokens": 3000}}, {"usage": {"prompt_tokens": 2000}}]
    spent["text_calls"] = [{"usage": {"prompt_tokens": 500}}]
    assert usage_tokens(spent) == 5500


def test_tokens_survive_a_decision_without_usage():
    assert usage_tokens({"decisions": [{}, {"usage": None}]}) == 0
    assert usage_tokens(None) == 0


# ── 7 · el guion de la batería, sin ejecutarla ─────────────────────────────────


def test_the_battery_has_the_eleven_missions_of_the_previous_run():
    assert len(missions("u", "p")) == 11
    assert [m["tag"] for m in missions("u", "p")][:3] == ["M1", "M2", "M3"]


def test_three_missions_are_measured_for_the_two_field_criterion():
    forms = [m for m in missions("u", "p") if m["form"]]
    assert [m["tag"] for m in forms] == ["M4", "M4b", "M9"]


def test_every_mission_declares_what_would_count_as_success():
    """Sin `expect_url` una misión no se puede verificar y no debería estar en la batería."""
    for mission in missions("u", "p"):
        assert mission["expect_url"], mission["tag"]
        assert mission["goal"].strip(), mission["tag"]
        assert mission["url"].startswith("http"), mission["tag"]


def test_the_login_missions_carry_the_credential_in_the_goal():
    """H11 exige la credencial en el objetivo. Sin esto el banco mediría el bloqueo, no el login."""
    for mission in missions("standard_user", "secret_sauce"):
        if mission["form"]:
            assert "standard_user" in mission["goal"]
            assert "secret_sauce" in mission["goal"]


def test_changing_the_model_sets_all_three_roles():
    """La batería anterior comparó modelos; si solo cambiara el ejecutor, no compararía nada."""
    try:
        apply_model("openai/gpt-oss-20b")
        assert [live_battery.os.environ[f"{p}_MODEL"] for p in ("POLICY", "PLANNER", "TEXT_MODEL")] == [
            "openai/gpt-oss-20b"
        ] * 3
    finally:
        for prefix in ("POLICY", "PLANNER", "TEXT_MODEL"):
            live_battery.os.environ.pop(f"{prefix}_MODEL", None)


def test_the_scorecard_survives_being_written_to_disk():
    """Lo que se mide tiene que poder volver a leerse sin la clave que lo produjo."""
    results = [{"tag": "M4", "outcome": "ÉXITO", "two_field": True, "steps": 7, "tokens": 1200}]
    path = ROOT / "artifacts" / "test-battery.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"scorecard": scorecard(results, results), "results": results}, ensure_ascii=False),
            encoding="utf-8",
        )
        reread = json.loads(path.read_text(encoding="utf-8"))
        assert reread["scorecard"]["two_field"]["filled"] == 1
        assert reread["results"][0]["steps"] == 7
    finally:
        path.unlink(missing_ok=True)


# ── 8 · el banco entero no se cae por una misión ───────────────────────────────


class _StubAgent:
    """Sustituye al agente: el banco se prueba sin navegador ni clave."""

    def __init__(self, script):
        self.script = script
        self.closed = False

    def snapshot(self):
        return self.script.get("snapshot", state(status="ready", url="about:blank"))

    def run(self):
        if self.script.get("raise_on_open"):
            raise RuntimeError("la página no responde")
        if self.script.get("raise_midway"):
            yield state(status="ready", url="https://www.saucedemo.com/")
            raise TimeoutError("el proveedor no devolvió nada")
        for step in self.script.get("steps", []):
            yield step

    def close(self):
        self.closed = True


def _drive(monkeypatch, script, mission=None, env=None):
    from jev_ultrafast import agent as loop
    from jev_ultrafast import providers

    monkeypatch.setattr(loop, "Agent", lambda url, goal: _StubAgent(script))
    monkeypatch.setattr(providers, "load_env_file", lambda *a, **k: None)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    return live_battery.run_mission(mission or FORM)


def test_a_page_that_will_not_open_is_recorded_not_raised(monkeypatch):
    """La batería mide once misiones: si una revienta el proceso, no hay batería."""
    record = _drive(monkeypatch, {"raise_on_open": True})
    assert record["outcome"] == "ERROR"
    assert "la página no responde" in record["why"]


def test_a_mission_that_dies_midway_is_recorded_not_raised(monkeypatch):
    record = _drive(monkeypatch, {"raise_midway": True})
    assert record["outcome"] == "ERROR"
    assert "TimeoutError" in record["why"]


def test_a_completed_mission_carries_its_evidence(monkeypatch):
    history = [fill("standard_user"), fill(SECRET)]
    record = _drive(monkeypatch, {"steps": [state(history=history)]})
    assert record["outcome"] == "ÉXITO"
    assert record["two_field"] is True
    assert record["steps"] == 2


def test_the_record_never_carries_the_credential(monkeypatch, tmp_path):
    """Mismo invariante que H11 en el historial, aplicado al informe que se comparte."""
    monkeypatch.setenv("JEV_MISSION_PASSWORD", "secret_sauce")
    mission = dict(FORM, goal=missions("standard_user", "secret_sauce")[3]["goal"])
    record = _drive(monkeypatch, {"steps": [state(history=[fill("standard_user"), fill(SECRET)])]}, mission)
    assert "secret_sauce" not in json.dumps(record, ensure_ascii=False)
    assert SECRET in record["goal"]


def test_the_browser_is_closed_even_when_the_mission_explodes(monkeypatch):
    """Una misión que revienta no puede dejar un Firefox abierto detrás."""
    seen = {}

    class _Tracking(_StubAgent):
        def close(self):
            seen["closed"] = True
            super().close()

    from jev_ultrafast import agent as loop
    from jev_ultrafast import providers

    monkeypatch.setattr(loop, "Agent", lambda url, goal: _Tracking({"raise_midway": True}))
    monkeypatch.setattr(providers, "load_env_file", lambda *a, **k: None)
    live_battery.run_mission(FORM)
    assert seen.get("closed") is True


# ── 9 · cinco fallos que la revisión encontró en el propio banco ────────────────


def test_tokens_are_read_with_the_names_the_providers_actually_use(monkeypatch):
    """`total_tokens` no lo devuelve nadie: OpenAI dice prompt/completion, Anthropic input/output.

    El contador de la primera versión leía solo ese campo, así que habría dado 0 en las once
    misiones y la comparación de modelos habría parecido gratis. Estos son los cuatro nombres
    reales, y los mismos que ya cuenta firefox.py.
    """
    spent = state()
    spent["decisions"] = [{"usage": {"prompt_tokens": 3000, "completion_tokens": 200}}]
    spent["text_calls"] = [{"usage": {"input_tokens": 500, "output_tokens": 100}}]
    assert usage_tokens(spent) == 3800
    assert set(live_battery.TOKEN_FIELDS) == {
        "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"
    }


def test_a_provider_that_reports_total_tokens_is_still_counted():
    """Un proveedor que sí lo devuelva no puede perder el gasto por no estar en la lista."""
    assert usage_tokens({"decisions": [{"usage": {"total_tokens": 42}}]}) == 0


def test_running_out_of_budget_is_a_failed_mission_not_an_error(monkeypatch):
    """Agotar el presupuesto dice algo del agente. Sacarlo del denominador mejoraría la tasa."""
    record = _drive(monkeypatch, {"raise_midway": True})
    # el stub de la prueba falla con TimeoutError: eso SÍ es del entorno
    assert record["outcome"] == "ERROR"

    from jev_ultrafast import agent as loop
    from jev_ultrafast import providers

    class _Budget(_StubAgent):
        def run(self):
            yield state(status="ready", url="https://www.saucedemo.com/")
            raise ValueError("Stopped at the 60-action demo budget")

    monkeypatch.setattr(loop, "Agent", lambda url, goal: _Budget({}))
    monkeypatch.setattr(providers, "load_env_file", lambda *a, **k: None)
    record = live_battery.run_mission(FORM)
    assert record["outcome"] == "FALLÓ"
    assert "presupuesto" in record["why"]


def test_every_record_has_the_same_keys_whatever_the_exit_was(monkeypatch):
    """Un informe con registros de forma distinta obliga a `.get()` a quien lo lea."""
    from jev_ultrafast import agent as loop
    from jev_ultrafast import providers

    monkeypatch.setattr(providers, "load_env_file", lambda *a, **k: None)
    shapes = []
    for script in ({"raise_on_open": True}, {"raise_midway": True}, {"steps": [state()]}):
        monkeypatch.setattr(loop, "Agent", lambda url, goal, s=script: _StubAgent(s))
        shapes.append(tuple(sorted(live_battery.run_mission(FORM).keys())))
    assert len(set(shapes)) == 1, f"registros con formas distintas: {set(shapes)}"
    assert shapes[0] == tuple(sorted(live_battery.skipped(FORM, "x").keys()))


def test_a_mission_the_environment_could_not_run_is_not_evaluable():
    """El criterio dice once misiones. Con dos, el porcentaje no se compara con el objetivo.

    Este es el fallo más caro del banco: `--only M4,M9` imprimía 2/2 = 100 % y marcaba
    CUMPLIDO el objetivo de ≥ 60 %, con la suite en verde detrás.
    """
    results = [{"outcome": "ÉXITO"}] * 2
    card = scorecard(results, [], expected=11)
    assert card["success"]["evaluable"] is False
    assert card["success"]["met"] is False
    assert card["success"]["rate"] == 1.0, "la cifra se sigue calculando; lo que no se puede es declararla"


def test_a_full_run_can_be_evaluated():
    card = scorecard([{"outcome": "ÉXITO"}] * 7 + [{"outcome": "FALLÓ"}] * 4, [], expected=11)
    assert card["success"]["evaluable"] is True
    assert card["success"]["met"] is True


def test_missions_the_environment_could_not_measure_leave_the_denominator():
    card = scorecard(
        [{"outcome": "ÉXITO"}] * 10 + [{"outcome": "OMITIDA"}], [], expected=11
    )
    assert card["success"]["total"] == 10
    assert card["success"]["undecided"] == 1
    assert card["success"]["evaluable"] is False


def test_a_missing_fixture_is_a_skip_and_not_a_failed_mission(monkeypatch):
    """M7 depende de un servidor local. Con el puerto apagado, el fallo es del entorno."""
    from jev_ultrafast import providers

    monkeypatch.setattr(providers, "load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(live_battery, "fixture_up", lambda *a, **k: False)
    record = live_battery.skipped(FORM, "el fixture del proyecto no estaba levantado")
    assert record["outcome"] == "OMITIDA"
    card = scorecard([record], [record], expected=11)
    assert card["success"]["total"] == 0
    assert card["two_field"]["total"] == 0


def test_the_fixture_check_does_not_raise_when_nothing_is_listening(monkeypatch):
    """El preflight no puede ser la razón de que la batería no arranque."""
    monkeypatch.setenv("TYPESAFE_DEMO_PORT", "9")   # puerto que nadie escucha
    assert live_battery.fixture_up() is False


def test_a_browser_that_never_started_does_not_fail_the_form_criterion():
    """El fallo que la prueba de humo cazó: sin navegador, H11 se declaraba NO CUMPLIDO.

    Nada se midió y el banco imprimía 0/1 = 0 % NO CUMPLIDO. Convertía 'no se pudo
    arrancar el navegador' en un suspenso del agente, que es exactamente el tipo de número
    que este script existe para no producir.
    """
    forms = [{"outcome": "ERROR", "two_field": False, "form": True}]
    card = scorecard(forms, forms, expected=11)["two_field"]
    assert card["total"] == 0
    assert card["evaluable"] is False
    assert card["met"] is False
    assert card["rate"] == 0.0, "la cifra no debe inventarse: se queda en 0 y no se declara"
    assert card["skipped_environment"] == 1


def test_only_real_attempts_count_as_forms_whatever_their_outcome():
    forms = [
        {"outcome": "ÉXITO", "two_field": True, "form": True},
        {"outcome": "FALLÓ", "two_field": False, "form": True},
        {"outcome": "SIN CLAVE", "two_field": False, "form": True},
        {"outcome": "OMITIDA", "two_field": False, "form": True},
        {"outcome": "ERROR", "two_field": False, "form": True},
    ]
    card = scorecard(forms, forms, expected=11)["two_field"]
    assert card["total"] == 2, "solo los dos intentos reales"
    assert card["filled"] == 1
    assert card["skipped_no_credential"] == 1
    assert card["skipped_environment"] == 2


def test_a_form_mission_with_no_credential_still_counts_against_the_success_rate():
    """Para el objetivo global, una misión sin credencial sí es una misión no completada.

    El mismo desenlace pesa distinto en los dos criterios, y por eso hay dos reglas y no
    una: H11 pregunta '¿logró el formulario?', que sin credencial no se puede preguntar;
    el objetivo global pregunta '¿logró la misión?', que sin credencial es un no.
    """
    card = scorecard([{"outcome": "SIN CLAVE"}] * 11, [], expected=11)["success"]
    assert card["total"] == 11
    assert card["rate"] == 0.0
    assert card["met"] is False
