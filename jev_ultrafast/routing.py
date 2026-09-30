"""H12 · enrutamiento por tarea: decidir cuánto cuesta cada decisión.

La batería de 11 misiones reales (Informe v2) dio dos medidas que apuntan al mismo sitio:

* El modelo rápido por defecto (**402 ms** en el ejecutor) se atasca en cuanto la misión
  deja de ser un clic evidente: vuelos pasó de imposible a **90 %** cambiando solo el modelo
  del ejecutor, con el mismo bucle y la misma clave (H12).
* El planificador **empeoró** las misiones: inventó `afrinic.net` y `booking.com` (H9), y el
  agente las siguió al pie de la letra hasta derivar. Y cuesta una llamada de razonamiento
  completa — **21 s** medidos en la instalación real, frente a los 402 ms del ejecutor.

La palanca, por tanto, no es "usar siempre un modelo mayor": es **no pagar el papel caro
cuando no compra nada**. Este módulo decide, sin llamar a ningún modelo, si una misión vale
la pena un plan o va directa, y con qué variante del ejecutor.

Reglas del módulo, por orden:

* **Determinista y sin red.** Una decisión de coste no puede depender de una llamada que
  cuesta 21 segundos, ni de un modelo que puede estar en cola.
* **Explicable.** Cada decisión devuelve *por qué*, para que el panel pueda decirlo en voz
  alta en vez de ocultarlo.
* **Reversible.** `JEV_ROUTING=always|never|auto` fija la política; `auto` es la que decide.
* Ante la duda, **planificar**. Un plan de más cuesta segundos; un plan de menos puede
  convertir una misión de cinco pasos en un bucle.
"""

import os
import re

# ── señales de que la misión es un solo gesto ─────────────────────────────────
# Una acción evidente: abrir, ir, ver, pulsar. Si solo hay una de estas y nada más, el
# planificador no añade nada: el ejecutor ya ve el objetivo en el siguiente paso.
SINGLE_ACT = re.compile(
    r"(?<![\w])("
    r"open|open the|go to|go to the|visit|browse|show me|view|see|look at|navigate to|"
    r"click|click on|press|tap|select|choose|"
    r"abre|abrir|ve a|ver|visita|navega a|pulsa|pulsar|haz clic|selecciona|"
    r"öffne|gehe zu|besuche|klicke|drücke|"
    r"ouvre|va à|aller à|visite|clique|appuie|"
    r"apri|aperta|clicca|premi|"
    r"abre|abrir|vai para|ver|visite|clique|pressione"
    r")(?![\w])",
    re.IGNORECASE,
)

# ── señales de que la misión tiene más de un paso ─────────────────────────────
# Un plan se paga cuando la misión encadena gestos: o el verbo lo dice ("iniciar sesión"
# son dos cosas: usuario y contraseña), o el texto las separa ("y", "and", "then", ","),
# o hay varios objetos que hay que tratar.
MULTI_ACT = re.compile(
    r"(?<![\w])("
    r"log ?in|log ?on|sign ?in|sign ?up|register|create an account|sign ?out|"
    r"book|reserve|checkout|check out|add to cart|add to basket|buy|purchase|pay|"
    r"search for|search and|look for|look up|find and|find all|compare|"
    r"fill in|fill out|complete the form|submit|"
    r"iniciar sesión|entrar|registrarse|crear una cuenta|reservar|comprar|pagar|"
    r"añadir al carrito|agregar al carrito|buscar y|buscar y abrir|"
    r"anmelden|einloggen|registrieren|buchen|bezahlen|warenkorb|"
    r"connexion|se connecter|inscription|réserver|acheter|payer|"
    r"accedi|accedere|acceder|registrarse|reservar|comprar|pagar"
    r")(?![\w])",
    re.IGNORECASE,
)

# Conectores que encadenan dos acciones en una sola frase.
CONJUNCTION = re.compile(r"(?<![\w])(and|then|after that|next|y|luego|después|entonces|und|dann|et|puis|"
                         r"e poi|depois)(?![\w])", re.IGNORECASE)

# Una coma seguida de otro verbo también encadena dos acciones: "reservar en Lisboa,
# abrir Casa Flora". Es una regla general, no un caso: en una misión, lo que va detrás de
# una coma suele ser el siguiente paso. Se mira hasta cuatro palabras después de la coma,
# porque el verbo puede ir primero ("abrir Casa Flora") o precedido ("y luego abrir…").
COMMA_THEN_VERB = re.compile(
    r",\s*(?:\S+\s+){0,3}?(?:" + SINGLE_ACT.pattern + "|" + MULTI_ACT.pattern + ")",
    re.IGNORECASE,
)

# Objetos repetidos: "de Madrid a Roma", "de 3 a 5 personas". Cada uno es un campo más.
REPEATED_ENTITY = re.compile(r"\b(\w+)\b(?:\s+(?:a|to|hasta|a|à|an|para|à)\s+(\w+)){2,}", re.IGNORECASE)

# Campos que una misión con formularios suele traer, aunque no los nombre.
FIELD_WORDS = re.compile(
    r"(?<![\w])(password|contraseña|contrasena|passwort|mot de passe|email|correo|e-mail|"
    r"card|tarjeta|credit|username|usuario|user|dob|born|birth|phone|tel|teléfono|"
    r"address|dirección|direccion|zip|código postal)(?![\w])",
    re.IGNORECASE,
)

# Un plan solo tiene sentido si cabe en el presupuesto de pasos que el bucle impone.
MAX_PLAN_STEPS = 12


def route(goal):
    """What a mission is worth, without asking a model anything.

    Returns a dict with `route` ("single" | "planned"), the `signals` that produced it and
    a one-line `reason` the sidebar can show. The reason is not decoration: a routing
    decision the user cannot see is a decision the user has to trust blindly.
    """
    text = (goal or "").strip()
    signals = []
    if not text:
        return {"route": "single", "signals": [], "reason": "empty goal"}

    if MULTI_ACT.search(text):
        signals.append("multi_step_verb")
    if CONJUNCTION.search(text):
        signals.append("conjunction")
    if COMMA_THEN_VERB.search(text):
        signals.append("comma_then_verb")
    if REPEATED_ENTITY.search(text):
        signals.append("several_targets")
    if FIELD_WORDS.search(text):
        signals.append("names_a_field")

    # Una enumeración larga ("busca X, Y, Z y W") también son varios pasos.
    if len(re.findall(r",", text)) >= 2:
        signals.append("long_list")

    if signals:
        return {
            "route": "planned",
            "signals": signals,
            "reason": f"mission with more than one step ({', '.join(signals)})",
        }

    if SINGLE_ACT.search(text):
        return {
            "route": "single",
            "signals": ["single_gesture"],
            "reason": "one gesture and nothing to sequence; the planner would only cost a call",
        }

    # Sin señales de ningún lado: noKnown. Planificar es lo seguro, porque un plan de más
    # cuesta segundos y uno de menos puede convertir la misión en un bucle.
    return {
        "route": "planned",
        "signals": ["unknown"],
        "reason": "no clear single gesture; planning is the safe side of the doubt",
    }


def needs_a_plan(goal, configured=None):
    """Whether to spend a planner call on this mission.

    `JEV_ROUTING` decides the policy: `always` keeps the old behaviour for everyone,
    `never` never plans, and `auto` (the default) reads the mission.
    """
    decision = route(goal)
    if configured is None:
        configured = (os.environ.get("JEV_ROUTING") or "auto").strip().lower()
    if configured in {"always", "on", "1", "yes"}:
        return True, {"route": "planned", "signals": ["forced"], "reason": "JEV_ROUTING=always"}
    if configured in {"never", "off", "0", "no"}:
        return False, {"route": "single", "signals": ["forced"], "reason": "JEV_ROUTING=never"}
    return decision["route"] == "planned", decision


def describe(decision):
    """The one line the sidebar shows, so the routing is never invisible."""
    return f"{decision['route']}: {decision['reason']}"
