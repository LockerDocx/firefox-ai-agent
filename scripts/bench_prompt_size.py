"""Mide el coste real del prompt del ejecutor, antes y después de recortarlo.

Reproduce el caso caro descrito en H6: una página densa cuyo snapshot llega al tope de
250 acciones, que es lo que el agente ve en buscador, listas y tablas. No hace red ni claves.

    python scripts/bench_prompt_size.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev_ultrafast.model import (  # noqa: E402
    PROMPT_ELEMENT_BUDGET,
    _policy_request,
    action_space,
    budget_actions,
    operation_catalog,
)

VIEWPORT = {"w": 1120, "h": 780}

ROLES = ["button", "link", "textbox", "combobox", "checkbox", "radio", "menuitem", "heading", "span", "img"]
LABELS = [
    "Search", "Search flights", "From", "To", "Departure date", "Return date", "Passengers",
    "Search button", "Sign in", "Create account", "Menu", "Close", "Next", "Back", "Apply",
    "Filter by price", "Sort by", "Add to cart", "Remove", "Quantity", "Checkout", "Add a photo",
    "Passport number", "Nationality", "Date of birth", "Email", "Password", "Remember me",
    "Help", "Terms", "Privacy", "Contact us", "Submit", "Cancel", "Save", "Edit", "Delete",
]

def dense_page(count=250, selects=6, options=28):
    """Una página tan densa como la peor que encontró la evaluación.

    Los desplegables nativos son el amplificador real: cada opción observada es una
    línea propia en el prompt, así que un solo <select> con 30 opciones lo llena entero.
    """
    actions = []
    for i in range(count):
        role = ROLES[i % len(ROLES)]
        label = f"{LABELS[i % len(LABELS)]} {i // len(LABELS) + 1}" if i >= len(LABELS) else LABELS[i]
        kind = "click"
        value = ""
        if role in ("textbox", "combobox"):
            kind = "fill"
        # La mitad de la página está fuera del viewport: es lo que hace que una tabla
        # entera entre en el prompt sin que nada de eso sea accionable ahora mismo.
        y = (i * 37) % 4200
        actions.append(
            {
                "id": f"e{i + 1}",
                "node": 1000 + i,
                "kind": kind,
                "role": role,
                "label": label,
                "value": value,
                "rect": {"x": 40 + (i % 7) * 150, "y": y, "w": 140, "h": 32},
            }
        )
    for i in range(0, count, 9):
        actions[i]["kind"] = "fill"
        actions[i]["role"] = "textbox"

    # Un desplegable nativo genera una acción por opción, cada una con su propia línea.
    for s in range(selects):
        node = 5000 + s
        base = {
            "id": f"sel{s}",
            "node": node,
            "role": "combobox",
            "label": LABELS[s % len(LABELS)],
            "current_value": "—",
            "rect": {"x": 60, "y": 120 + s * 40, "w": 260, "h": 30},
        }
        for o in range(options):
            actions.append(
                {
                    **base,
                    "id": f"sel{s}o{o}",
                    "kind": "select",
                    "value": f"opt_{o}",
                    # Etiquetas de opción cortas, como las de un desplegable real. El
                    # prefijo del elemento lo pone snapshot.js ("Etiqueta → Opción").
                    "label": f"{base['label']} → Opción {o}",
                }
            )

    state = {
        "url": "https://www.example-booking.test/flights/search?adults=1",
        "title": "Book a flight — results",
        "text": "\n".join(f"row {i}: fare option {i} EUR, carrier {i % 9}" for i in range(220)),
        "w": VIEWPORT["w"],
        "h": VIEWPORT["h"],
        "actions": actions,
    }
    return state

def history(n=10):
    return [
        {
            "step": i + 1,
            "action": f"Search flights {i}",
            "kind": "click" if i % 2 else "fill",
            "text": "Zúrich" if i == 0 else None,
            "page_changed": True,
        }
        for i in range(n)
    ]

GOAL = "search Google Flights for Zürich to London next Sunday, one adult"

def measure(state, goal=GOAL, hist=None, budget=None):
    """The exact string the provider receives in the user turn."""
    hist = history() if hist is None else hist
    if budget is None:
        actions = state["actions"]
        omitted = 0
    else:
        actions, omitted = budget_actions(actions=state["actions"], state=state, history=hist, limit=budget)
    elements, targets, controls = action_space(actions)
    operations = operation_catalog(targets, controls)
    user = _policy_request(goal, state, elements, operations, hist, omitted)
    return {
        "chars": len(user),
        "lines": user.count("\n") + 1,
        "elements": len(elements),
        "omitted": omitted,
        "tokens_est": round(len(user) / 4),
        "user": user,
    }

def in_viewport(action, state):
    rect = action.get("rect") or {}
    if not rect:
        return False
    top = rect.get("y", 0)
    return -state["h"] <= top <= 2 * state["h"]

def main():
    print("=" * 78)
    print("Coste del prompt del ejecutor · H6")
    print("=" * 78)
    state = dense_page(250)
    result = measure(state)
    baseline = result["chars"]
    print(f"Página densa: {len(state['actions'])} acciones observadas")
    print(f"  prompt  : {result['chars']:,} caracteres · {result['lines']} líneas")
    print(f"  elementos: {result['elements']} en la tabla")
    print(f"  tokens  : ~{result['tokens_est']:,} (estimación a 4 chars/token)")
    print()
    print(f"  dentro del viewport hoy: {sum(1 for a in state['actions'] if in_viewport(a, state))}")
    print()

    # Coste por bloque, para saber dónde está el dinero
    user = result["user"]
    blocks = {}
    marker = "ELEMENTS (index"
    head = user[: user.index(marker)] if marker in user else user
    rest = user[user.index(marker) :] if marker in user else ""
    blocks["cabecera + texto de página"] = len(head)
    table = rest.split("AVAILABLE OPERATIONS")[0] if "AVAILABLE OPERATIONS" in rest else rest
    blocks["tabla de elementos"] = len(table)
    blocks["operaciones + historial"] = len(rest) - blocks["tabla de elementos"]
    for name, size in blocks.items():
        print(f"  {name:32s} {size:>7,} chars  {size / baseline * 100:5.1f}%")
    print()

    # ¿Cuánto cuesta una página normal, no patológica?
    print()
    print("SIN presupuesto (lo que hacía antes):")
    for count in (40, 80, 120, 180, 250):
        r = measure(dense_page(count))
        print(f"  {count:>3} acciones -> {r['chars']:>7,} chars  ~{r['tokens_est']:>6,} tokens")
    print()
    print(f"CON presupuesto de {PROMPT_ELEMENT_BUDGET} elementos (H6):")
    for count in (40, 80, 120, 180, 250):
        r = measure(dense_page(count), budget=PROMPT_ELEMENT_BUDGET)
        print(
            f"  {count:>3} acciones -> {r['chars']:>7,} chars  ~{r['tokens_est']:>6,} tokens"
            f"   ({r['elements']} elementos, {r['omitted']} omitidos)"
        )

    after = measure(dense_page(250), budget=PROMPT_ELEMENT_BUDGET)["chars"]
    print()
    print("Curva de compromiso (página densa de 250 acciones + 6 desplegables):")
    print("  Cada desplegable que se enseña cuesta sus ~28 líneas de opciones.")
    print("  Enseñar menos desplegables abarata el prompt y estrecha la visión del modelo.")
    print()
    print(f"  {'opciones':>9} {'chars':>9} {'tokens':>8} {'ahorro':>8} {'desplegables':>13}")
    page = dense_page(250)
    for option_limit in (0, 30, 60, 90, 120, 150, 400):
        kept, _ = budget_actions(page["actions"], page, limit=PROMPT_ELEMENT_BUDGET,
                                 option_limit=option_limit)
        elements, targets, controls = action_space(kept)
        user = _policy_request(GOAL, page, elements, operation_catalog(targets, controls), history(), 0)
        dropdowns = sum(1 for e in elements if "SELECT" in e["operations"])
        saving = (1 - len(user) / baseline) * 100
        print(f"  {option_limit:>9} {len(user):>9,} {round(len(user) / 4):>8,} {saving:>7.1f}% {dropdowns:>13}")
    print()
    print("=" * 78)
    print(f"Línea base   : {baseline:,} caracteres (~{round(baseline / 4):,} tokens)")
    print(f"Con H6       : {after:,} caracteres (~{round(after / 4):,} tokens)")
    saving = (1 - after / baseline) * 100
    print(f"Ahorro       : {saving:.1f}%   ·  objetivo H6: −50%   →  {'CUMPLIDO' if saving >= 50 else 'NO CUMPLIDO'}")
    target = 12_000
    print(f"Techo H6     : ≤ {target:,} caracteres  →  {'CUMPLIDO' if after <= target else 'NO CUMPLIDO'}")
    print("=" * 78)
    return baseline

if __name__ == "__main__":
    main()
