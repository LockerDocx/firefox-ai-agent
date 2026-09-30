"""H6 · el presupuesto del prompt del ejecutor: que abarate sin romper nada.

Un recorte que dejara un índice apuntando a un elemento que ya no existe, o que dejara
un desplegable sin la opción correcta, no sería una mejora: sería un fallo más caro y
más difícil de ver. Estos tests fijan las dos cosas.
"""

import json

import pytest

from jev_ultrafast.model import (
    POLICY_SYSTEM,
    PROMPT_ELEMENT_BUDGET,
    PROMPT_OPTION_BUDGET,
    _policy_request,
    action_space,
    budget_actions,
    operation_catalog,
)

VIEWPORT = {"w": 1120, "h": 780}


def action(node, kind="click", role="button", label="Button", y=100, **extra):
    return {
        "id": f"e{node}",
        "node": node,
        "kind": kind,
        "role": role,
        "label": label,
        "value": "",
        "rect": {"x": 20, "y": y, "w": 120, "h": 30},
        **extra,
    }


def state_of(actions, **extra):
    return {
        "url": "https://example.test/",
        "title": "Example",
        "text": "page text",
        "w": VIEWPORT["w"],
        "h": VIEWPORT["h"],
        "actions": actions,
        **extra,
    }


def prompt_for(actions, state=None, history=()):
    state = state or state_of(actions)
    kept, omitted = budget_actions(actions, state, history)
    elements, targets, controls = action_space(kept)
    operations = operation_catalog(targets, controls)
    return _policy_request("do the thing", state, elements, operations, history, omitted), kept, omitted


# ── el presupuesto no debe cambiar una página que ya cabe ──────────────────────


def test_a_page_within_the_budget_is_untouched():
    actions = [action(1000 + i, label=f"Button {i}", y=i * 10) for i in range(PROMPT_ELEMENT_BUDGET)]
    kept, omitted = budget_actions(actions, state_of(actions))
    assert kept == actions, "una página que ya cabe no debe tocar una sola acción"
    assert omitted == 0


def test_a_budget_of_none_disables_the_cut():
    actions = [action(2000 + i, y=i * 10) for i in range(500)]
    kept, omitted = budget_actions(actions, state_of(actions), limit=None)
    assert kept == actions
    assert omitted == 0


# ── corrección: nada de índices que apunten al vacío ────────────────────────────


def test_every_prompted_index_maps_to_a_real_action():
    """La garantía central: lo que el modelo ve existe, y se puede ejecutar."""
    actions = [action(3000 + i, role="button" if i % 3 else "textbox",
                      kind="click" if i % 3 else "fill", label=f"Control {i}", y=(i * 53) % 5000)
               for i in range(400)]
    state = state_of(actions)
    kept, _ = budget_actions(actions, state)
    elements, targets, controls = action_space(kept)

    by_id = {a["id"]: a for a in kept}
    for element in elements:
        assert int(element["index"]) >= 1
        for operation in element["operations"]:
            assert element["index"] in targets[operation], f"índice {element['index']} sin destino en {operation}"
            assert targets[operation][element["index"]]["id"] in by_id
    for operation, group in targets.items():
        for target, chosen in group.items():
            assert chosen["id"] in by_id, f"destino {target} de {operation} no está en las acciones observadas"


def test_the_chosen_id_is_still_resolvable_in_the_full_page():
    """agent.py busca el id elegido en page['actions']; el recorte no puede romperlo."""
    actions = [action(4000 + i, y=(i * 31) % 6000) for i in range(300)]
    state = state_of(actions)
    kept, _ = budget_actions(actions, state)
    elements, targets, _ = action_space(kept)
    for operation, group in targets.items():
        for chosen in group.values():
            assert chosen["id"] in {a["id"] for a in actions}


# ── corrección: un desplegable nunca pierde opciones ────────────────────────────


def a_select(node, options, y=100, label="Passengers"):
    """Un <select> nativo: una acción por opción, todas con el mismo node."""
    return [
        action(node, kind="select", role="combobox", label=f"{label} → {name}", y=y, value=value)
        for name, value in options
    ]


def test_a_kept_dropdown_keeps_every_option():
    options = [(f"Option {i}", f"opt_{i}") for i in range(12)]
    actions = a_select(5000, options)
    kept, _ = budget_actions(actions, state_of(actions))
    assert len(kept) == len(options), "un desplegable que entra, entra entero"


def test_a_dropdown_is_never_left_without_its_options():
    """El modo de fallo caro: un select visible al que no se le puede elegir nada."""
    actions = [action(6000 + i, y=(i * 17) % 5000) for i in range(300)]
    for s in range(5):
        actions += a_select(7000 + s, [(f"Opt {o}", f"o{o}") for o in range(20)], y=80 + s * 30, label=f"Menu {s}")
    state = state_of(actions)
    kept, _ = budget_actions(actions, state)
    elements, targets, _ = action_space(kept)
    # action_space no expone el node en el elemento, así que se empareja por etiqueta:
    # las opciones se llaman "Passengers → Opt 0", y el elemento se llama "Passengers".
    observed_options = {}
    for a in actions:
        if a["kind"] == "select":
            base = a["label"].split(" → ")[0]
            observed_options[base] = observed_options.get(base, 0) + 1
    checked = 0
    for element in elements:
        if "SELECT" not in element["operations"]:
            continue
        rendered = len(element.get("options", []))
        observed = observed_options[element["label"]]
        assert observed > 0
        assert rendered == observed, (
            f"elemento {element['label']}: {rendered} de {observed} opciones — no se puede recortar un desplegable"
        )
        checked += 1
    assert checked, "la página del test tiene desplegables; el test no debe pasar por no mirar ninguno"


# ── corrección: la memoria del bucle sobrevive al presupuesto ───────────────────


def test_what_the_run_just_did_is_never_dropped():
    actions = [action(8000 + i, y=(i * 23) % 6000) for i in range(300)]
    target = actions[280]  # el último, el peor puntuado
    history = [{"step": 1, "node": target["node"], "action": target["label"], "kind": "click"}]
    kept, _ = budget_actions(actions, state_of(actions), history)
    assert target["id"] in {a["id"] for a in kept}, "el elemento que el bucle está usando no puede desaparecer"


# ── el recorte se declara, no se esconde ────────────────────────────────────────


def test_the_omission_is_stated_in_the_prompt():
    actions = [action(9000 + i, y=(i * 19) % 6000) for i in range(400)]
    user, _kept, omitted = prompt_for(actions)
    assert omitted > 0
    assert f"(+{omitted} more observed elements" in user
    assert "scroll" in user.lower(), "el modelo debe saber que puede llegar a lo omitido"


def test_a_full_page_never_claims_an_omission():
    actions = [action(9500 + i, y=i * 10) for i in range(20)]
    user, _kept, omitted = prompt_for(actions)
    assert omitted == 0
    assert "more observed elements" not in user


# ── el efecto medido: el objetivo de H6 ─────────────────────────────────────────


def dense(count=250, selects=6, options=28):
    actions = [action(10_000 + i, role="button" if i % 3 else "textbox",
                      kind="click" if i % 3 else "fill", label=f"Control {i}", y=(i * 37) % 4200)
               for i in range(count)]
    for s in range(selects):
        actions += a_select(20_000 + s, [(f"Option {o}", f"opt_{o}") for o in range(options)], y=120 + s * 40)
    return actions


def cost_of(count):
    actions = dense(count)
    user, _kept, _omitted = prompt_for(actions)
    return len(user)


def test_the_prompt_is_bounded_whatever_the_page_holds():
    """Antes, el prompt crecía con la página: 6.100 tokens en la peor. Ahora tiene techo."""
    small, large = cost_of(40), cost_of(600)
    assert large <= small * 1.25, f"el prompt sigue creciendo con la página: {small} -> {large}"


def test_the_prompt_lands_under_the_h6_ceiling():
    """Criterio de aceptación de H6: ≤ 12.000 caracteres (≤ 3.000 tokens) en una página densa."""
    assert cost_of(250) <= 12_000, f"el prompt sigue en {cost_of(250)} caracteres"


def test_the_budget_actually_saves_at_least_half():
    unbounded, bounded = [], []
    for count in (40, 80, 120, 180, 250):
        actions = dense(count)
        state = state_of(actions)
        elements, targets, controls = action_space(actions)  # sin presupuesto
        unbounded.append(len(_policy_request("g", state, elements, operation_catalog(targets, controls), ())))
        bounded.append(cost_of(count))
    saving = 1 - bounded[-1] / unbounded[-1]
    assert saving >= 0.5, f"solo se ahorra un {saving * 100:.1f}% en la página más densa"


# ── el ahorro easy que salió de la propia medición ──────────────────────────────


def test_an_option_line_does_not_repeat_its_element_label():
    """Cada línea de opción repetía la etiqueta del elemento; era gasto puro."""
    actions = a_select(30_000, [("Adults", "adults"), ("Children", "children")])
    elements, _targets, _controls = action_space(actions)
    labels = [option["label"] for option in elements[0]["options"]]
    assert labels == ["Adults", "Children"]
    assert not any(label.startswith("Passengers") for label in labels)


def test_the_element_itself_still_shows_its_full_label():
    actions = a_select(31_000, [("Adults", "adults")])
    elements, _targets, _controls = action_space(actions)
    assert elements[0]["label"] == "Passengers"


# ── el presupuesto es configurable y está acotado ────────────────────────────────


def test_both_budgets_exist_and_are_sane():
    assert 0 < PROMPT_ELEMENT_BUDGET <= 250, "el tope del snapshot es 250; un presupuesto mayor no recorta"
    assert 0 < PROMPT_OPTION_BUDGET


def test_the_result_carries_the_cost_so_the_panel_can_show_it():
    """El recorte solo es creíble si se puede medir en la ejecución."""
    from jev_ultrafast.model import provider_choose

    source = open(__file__, encoding="utf-8").read()
    assert "omitted_elements" in open(
        "jev_ultrafast/model.py", encoding="utf-8"
    ).read(), "provider_choose debe reportar lo que omitió"
    assert provider_choose is not None and source


def test_the_system_prompt_is_unchanged_and_still_carries_the_contracts():
    """Recortar el prompt de usuario no puede tocar los contratos del sistema."""
    for contract in ("CLICK", "TYPE_TEXT", "SELECT", "SCROLL_UP", "DONE", "BLOCKED", "untrusted"):
        assert contract in POLICY_SYSTEM
    assert "Never invent elements" in POLICY_SYSTEM


def test_a_json_operation_answer_still_validates_against_the_budgeted_targets():
    actions = dense(250)
    kept, _ = budget_actions(actions, state_of(actions))
    _elements, targets, _controls = action_space(kept)
    from jev_ultrafast.model import _validate_llm_choice

    operations = operation_catalog(targets, {})
    # Un objetivo real de la tabla presupuestada, no uno supuesto: el índice 1 es el
    # primer campo de texto, y por tanto no admite CLICK.
    operation = "CLICK"
    target = next(iter(targets[operation]))
    chosen, resolved, confidence = _validate_llm_choice(
        {"operation": operation, "target": target, "confidence": 0.9}, operations, targets
    )
    assert chosen == operation
    assert targets[operation][resolved]["id"] in {a["id"] for a in kept}
    assert confidence == 0.9

    # Un índice que el modelo no vio nunca se rechaza, igual que antes del presupuesto.
    with pytest.raises(ValueError):
        _validate_llm_choice({"operation": operation, "target": "99999"}, operations, targets)


def test_a_select_answer_is_only_valid_for_an_option_that_was_shown():
    actions = a_select(40_000, [(f"Opt {o}", f"o{o}") for o in range(5)])
    kept, _ = budget_actions(actions, state_of(actions))
    _elements, targets, _controls = action_space(kept)
    from jev_ultrafast.model import _validate_llm_choice

    operations = operation_catalog(targets, {})
    operation, target, _ = _validate_llm_choice(
        {"operation": "SELECT", "target": "1:3", "confidence": 1.0}, operations, targets
    )
    assert target == "1:3"
    assert json.loads(json.dumps(targets[operation][target]["value"])) == "o2"
