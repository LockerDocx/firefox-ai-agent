"""Batería viva: convierte en cifra los dos criterios que siguen pendientes.

La auditoría independiente de v0.13.1 (01/10/2026) llega a lo mismo:

  * la tasa de éxito de misión, objetivo ≥ 60 % sobre once misiones;
  * el criterio de H11, completar el 100 % de los formularios de dos campos.

Los dos necesitan navegador y clave reales, y por eso ningún test los puede cerrar. Lo que
**sí** queda fuera del campo es la lógica de veredicto, y es la parte que se equivoca: un
"éxito" escrito a ojo o un formulario contado a mano. Aquí el resultado se decide contra la
URL final y contra el historial, y la lógica esa es pura y vive en tests sin red ni claves.

    python scripts/live_battery.py                  # las once misiones
    python scripts/live_battery.py --only M4,M9     # solo las de formulario
    python scripts/live_battery.py --model openai/gpt-oss-20b

Credenciales: se leen del entorno, nunca del repositorio. Para las misiones de saucedemo
hacen falta las dos, y sin ellas el resultado NO es un fallo del agente sino el
comportamiento de H11 por diseño: parar y decirlo en vez de inventar una clave.

    JEV_MISSION_USER / JEV_MISSION_PASSWORD
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
# La máscara con la que el historial registra lo tecleado en un campo secreto. Es la misma
# constante que escribe agent.py, y es la prueba de que una contraseña se escribió sin que
# nadie pueda leerla: el valor nunca sale del navegador.
SECRET = "•••"

SUCCESS_TARGET = 0.60
TWO_FIELD_TARGET = 1.00

SAUCEDEMO = "https://www.saucedemo.com/"


def missions(user="", password=""):
    """Las once misiones de la batería, con su comprobación mecánica de éxito.

    `expect` no es una impresión: es lo que tiene que ser verdad de la URL final para que la
    misión cuente como completada. Sin ella, "el agente dijo DONE" sería el criterio, y DONE
    es justo lo que se cuenta mal.
    """
    login = f"Login con usuario {user} y contraseña {password}, añadir una mochila al carrito"
    login_steps = (
        f"Login con usuario {user} y contraseña {password} paso a paso, "
        "añadir una mochila al carrito y abrir el carrito"
    )
    return [
        {
            "tag": "M1", "site": "example.com", "url": "https://example.com/",
            "goal": "Pulsar el enlace de información y llegar a iana.org",
            "expect_url": "iana.org", "form": False,
        },
        {
            "tag": "M2", "site": "DDG Lite", "url": "https://lite.duckduckgo.com/lite/",
            "goal": "Buscar NVIDIA NIM y abrir el primer resultado",
            "expect_url": "nvidia.com", "form": False,
        },
        {
            "tag": "M3", "site": "Wikipedia ES", "url": "https://es.wikipedia.org/wiki/Especial:Buscar",
            "goal": "Buscar y abrir el artículo de Yerba mate",
            "expect_url": "yerba", "form": False,
        },
        {
            "tag": "M4", "site": "saucedemo", "url": SAUCEDEMO,
            "goal": login, "expect_url": "cart.html", "form": True,
        },
        {
            "tag": "M4b", "site": "saucedemo guiada", "url": SAUCEDEMO,
            "goal": login_steps, "expect_url": "cart.html", "form": True,
        },
        {
            "tag": "M5", "site": "GitHub", "url": "https://github.com/LockerDocx/firefox-ai-agent",
            "goal": "Abrir la página de Releases del repositorio",
            "expect_url": "releases", "form": False,
        },
        {
            "tag": "M6", "site": "Flights natural", "url": "https://www.google.com/travel/flights?hl=es",
            "goal": "Vuelo Zúrich-Londres para un adulto",
            "expect_url": "tfs=", "form": False,
        },
        {
            "tag": "M6b", "site": "Flights guiada", "url": "https://www.google.com/travel/flights?hl=es",
            "goal": "Vuelo Zúrich-Londres para un adulto, paso a paso",
            "expect_url": "tfs=", "form": False,
        },
        {
            "tag": "M7", "site": "fixture del proyecto",
            "url": "http://127.0.0.1:8766/fixture.html?scenario=flights",
            "goal": "Design stays en Lisboa con cancelación, abrir Casa Flora",
            "expect_url": "casa-flora", "form": False,
        },
        {
            "tag": "M8", "site": "Flights (2º modelo)", "url": "https://www.google.com/travel/flights?hl=es",
            "goal": "Vuelo Zúrich-Londres para un adulto",
            "expect_url": "tfs=", "form": False,
        },
        {
            "tag": "M9", "site": "saucedemo (2º modelo)", "url": SAUCEDEMO,
            "goal": login, "expect_url": "cart.html", "form": True,
        },
    ]


# ── 1 · la parte que decide, y que se prueba sin red ───────────────────────────


def masked_goal(goal, password):
    """El objetivo viaja al informe JSON, y el objetivo lleva la credencial dentro.

    El informe se escribe en disco y es lo primero que se comparte para discutir un
    resultado. Un informe de batería que contenga la contraseña de la misión es el mismo
    fallo que H11 arregló en el historial, otro día y en otro archivo.
    """
    return goal.replace(password, SECRET) if password else goal


def typed_fields(history):
    """Qué se escribió de verdad: un paso es 'fill' y su texto no está enmascarado."""
    return [h for h in (history or []) if h.get("kind") == "fill" and h.get("text") not in (None, "")]


def typed_a_secret(history):
    """¿Se escribió en un campo de contraseña? La máscara es la prueba.

    El historial nunca guarda el valor: guarda '•••'. Así que un fill enmascarado prueba las
    dos cosas a la vez — que la contraseña se escribió, y que no se puede leer.
    """
    return any(h.get("kind") == "fill" and h.get("text") == SECRET for h in (history or []))


def two_field_evidence(history):
    """¿Pasaron los dos campos del formulario, usuario y contraseña?"""
    fills = typed_fields(history)
    return bool([h for h in fills if h.get("text") != SECRET]) and typed_a_secret(history)


def verdict(mission, state):
    """El resultado de una misión, contra su URL esperada y no contra lo que dijo el agente.

    Tres desenlaces distintos y no intercambiables, en este orden:
      SIN CLAVE no había credencial y el agente paró diciéndolo, que es H11 por diseño;
      ÉXITO    la URL final cumple lo que la misión pedía;
      FALLÓ    el bucle terminó sin cumplirlo.

    Que ÉXITO no mire el estado final es deliberado: una misión que llegó a su destino y
    después se atascó tres acciones sin cambiar nada **ha cumplido el objetivo**. Medir el
    ánimo con el que terminó el bucle en vez del estado en el que está la página daría una
    tasa que depende del presupuesto que sobró, no de lo que el agente sabe hacer.
    """
    history = (state or {}).get("history") or []
    status = (state or {}).get("status")
    url = ((state or {}).get("page") or {}).get("url") or (history[-1].get("url") if history else "") or ""
    blocked = (state or {}).get("blocked_reason")
    if status == "blocked" and blocked and "does not supply" in blocked:
        return "SIN CLAVE", "el objetivo no traía la credencial y el agente paró en vez de inventarla"
    if mission["expect_url"] and mission["expect_url"] in url:
        return "ÉXITO", url
    if mission.get("form") and status == "blocked" and blocked:
        return "FALLÓ", blocked
    if status in {"done", "blocked"}:
        return "FALLÓ", f"terminó en '{status}' sin llegar a «{mission['expect_url']}» ({url})"
    return "FALLÓ", f"el bucle no terminó (estado '{status}')"


# Los cuatro nombres con los que los proveedores nombran lo que gastan. OpenAI dice
# prompt/completion, Anthropic input/output, y ninguno de los dos dice 'total_tokens':
# un contador que leyera solo ese campo daría 0 en todas las misiones y parecería que el
# modelo es gratis. Los mismos cuatro campos que ya cuenta firefox.py para el pie del panel.
TOKEN_FIELDS = ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens")


def usage_tokens(state):
    """Tokens de las decisiones y del ayudante de texto, para comparar coste entre modelos."""
    entries = list((state or {}).get("decisions") or []) + list((state or {}).get("text_calls") or [])
    total = 0
    for entry in entries:
        usage = entry.get("usage") or {}
        for field in TOKEN_FIELDS:
            value = usage.get(field)
            if isinstance(value, (int, float)):
                total += value
    return total


def scorecard(results, forms, expected=None):
    """Los dos criterios, con el denominador que cada uno merece.

    `expected` es cuántas misiones tenía que tener la batería para que el criterio signifique
    algo. El criterio de aceptación dice 'once misiones': medir dos y llamar '100 %' a eso
    es un número que no se puede comparar con el objetivo, y un banco que lo imprimiera como
    CUMPLIDO estaría mintiendo con un tests verde detrás. Cuando el denominador no da, la
    respuesta es 'no evaluable', no un veredicto.
    """
    scored = [r for r in results if r["outcome"] not in {"ERROR", "OMITIDA"}]
    ok = sum(1 for r in scored if r["outcome"] == "ÉXITO")
    rate = ok / len(scored) if scored else 0.0

    # H11 solo se mide sobre las misiones de formulario, y una misión sin credencial no cuenta
    # como formulario fallido: cuenta como 'no medible', que es lo que H11 hace al parar.
    # Al denominador de H11 solo entran los intentos reales de formulario. Ni 'SIN CLAVE'
    # (no había credencial), ni 'OMITIDA' (el entorno no dejó intentarlo), ni 'ERROR' (el
    # navegador ni siquiera arrancó) son formularios fallidos: contarlos convertiría una
    # carencia del banco en un suspenso del agente. Una prueba de humo con el navegador sin
    # lanzar llegó a imprimir 'H11 NO CUMPLIDO 0/1' sin haber medido un solo formulario.
    runnable = [r for r in forms if r["outcome"] in {"ÉXITO", "FALLÓ"}]
    filled = sum(1 for r in runnable if r["two_field"])
    two_field = filled / len(runnable) if runnable else 0.0
    # Lo que tiene que dar el número son veredictos, no filas del informe: once misiones
    # con una omitida son diez measurements, y un 10/10 no es el criterio de once.
    whole = expected is None or len(scored) >= expected
    return {
        "success": {
            "ok": ok, "total": len(scored), "rate": rate, "target": SUCCESS_TARGET,
            "evaluable": whole, "met": whole and rate >= SUCCESS_TARGET, "expected": expected,
            "undecided": len(results) - len(scored),
        },
        "two_field": {
            "filled": filled, "total": len(runnable), "rate": two_field, "target": TWO_FIELD_TARGET,
            "skipped_no_credential": sum(1 for r in forms if r["outcome"] == "SIN CLAVE"),
            "skipped_environment": sum(1 for r in forms if r["outcome"] in {"OMITIDA", "ERROR"}),
            "evaluable": bool(runnable), "met": two_field >= TWO_FIELD_TARGET and bool(runnable),
        },
    }


# ── 2 · la parte que necesita el navegador y la clave ───────────────────────────


def fixture_up(port=None):
    """¿Está el fixture del proyecto levantado? M7 depende de él y sin él no se puede medir.

    Es la misma clase que una clave sin configurar: una carencia del entorno, no una
    capacidad del agente. Se comprueba antes de gastar minutos en una misión que acabaría
    en 'no se pudo abrir la página' y rebajaría la tasa por un servidor apagado.
    """
    import urllib.error
    import urllib.request

    number = int(port or os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{number}/api/state", timeout=1.5) as answer:
            return 200 <= answer.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def skipped(mission, why):
    """Una misión que no se midió. No es un fallo, y no va en ningún denominador."""
    return {
        "tag": mission["tag"], "site": mission["site"], "goal": "", "form": mission["form"],
        "outcome": "OMITIDA", "why": why, "steps": 0, "tokens": 0, "elapsed_s": 0.0,
        "route": None, "two_field": False, "url": "", "status": None, "blocked_reason": None,
    }


def run_mission(mission, timeout_s=900):
    """Una misión, de principio a fin. Nunca aborta la batería: un fallo es un dato."""
    from jev_ultrafast import providers
    from jev_ultrafast.agent import Agent

    providers.load_env_file()
    started = time.perf_counter()
    password = os.environ.get("JEV_MISSION_PASSWORD", "").strip()
    record = {
        "tag": mission["tag"], "site": mission["site"],
        "goal": masked_goal(mission["goal"], password),
        "form": mission["form"], "outcome": "ERROR", "why": "", "steps": 0,
        "tokens": 0, "elapsed_s": 0.0, "route": None, "two_field": False, "url": "",
        "status": None, "blocked_reason": None,
    }
    try:
        agent = Agent(mission["url"], mission["goal"])
    except Exception as error:  # una página que no abre es un resultado, no una excepción
        record["why"] = f"no se pudo abrir la página: {error}"
        record["elapsed_s"] = round(time.perf_counter() - started, 1)
        return record
    try:
        state = agent.snapshot()
        for state in agent.run():
            if time.perf_counter() - started > timeout_s:
                # Un corte por reloj no es un veredicto del agente: se registra aparte para
                # que no se cuente como mission failed ni como mission succeeded.
                record["outcome"] = "ERROR"
                record["why"] = f"superó el límite de {timeout_s} s sin terminar"
                break
        else:
            record["outcome"], record["why"] = verdict(mission, state)
        record["route"] = state.get("route")
        record["steps"] = len(state.get("history") or [])
        record["tokens"] = usage_tokens(state)
        record["two_field"] = two_field_evidence(state.get("history"))
        record["url"] = ((state.get("page") or {}).get("url") or "")
        record["status"] = state.get("status")
        record["blocked_reason"] = state.get("blocked_reason")
    except Exception as error:
        # Quedarse sin presupuesto de pasos es un veredicto del agente, no un fallo del
        # entorno: si el bucle no avanzó dentro del presupuesto, la misión está fallada.
        # Marcarlo como error lo sacaría del denominador y mejoraría la tasa sin motivo.
        reason = str(error)
        if "budget" in reason:
            record["outcome"] = "FALLÓ"
            record["why"] = f"se agotó el presupuesto: {reason}"
        else:
            record["why"] = f"{type(error).__name__}: {reason}"
    finally:
        try:
            agent.close()
        except Exception:
            pass
        record["elapsed_s"] = round(time.perf_counter() - started, 1)
    return record


def apply_model(name):
    """Poner un segundo modelo en las tres variables de rol, como hizo la batería anterior."""
    for prefix in ("POLICY", "PLANNER", "TEXT_MODEL"):
        os.environ[f"{prefix}_MODEL"] = name
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="solo estas misiones, separadas por comas (M4,M9)")
    parser.add_argument("--model", help="modelo para política, planificador y texto")
    parser.add_argument("--out", help="dónde escribir el informe JSON")
    parser.add_argument("--timeout", type=int, default=900, help="segundos por misión")
    args = parser.parse_args(argv)

    from jev_ultrafast import providers
    from jev_ultrafast.model import policy_description

    providers.load_env_file()
    # interactive=False: un pegado de clave a mitad de una batería de 15 minutos sería un
    # banco colgado, no un banco midiendo.
    if not providers.ensure_configured(interactive=False):
        print("No hay clave de proveedor configurada.")
        print("  TYPESAFE_API_KEY, o POLICY_PROVIDER + POLICY_API_KEY / POLICY_MODEL.")
        print("  Sin clave no hay batería: esto mide decisiones del modelo, no el navegador.")
        return 2
    if args.model:
        apply_model(args.model)

    user = os.environ.get("JEV_MISSION_USER", "").strip()
    password = os.environ.get("JEV_MISSION_PASSWORD", "").strip()
    chosen = missions(user, password)
    if args.only:
        wanted = {t.strip().upper() for t in args.only.split(",") if t.strip()}
        chosen = [m for m in chosen if m["tag"] in wanted]
        if not chosen:
            print(f"Ninguna misión coincide con {args.only}.")
            return 2

    print("=" * 96)
    print("Batería viva · los dos criterios que la auditoría dejó sin cifra")
    print("=" * 96)
    print(f"  modelo  : {policy_description()}")
    print(f"  misiones: {len(chosen)}   credencial de formulario: "
          f"{'configurada' if password else 'NO — las misiones de formulario saldrán SIN CLAVE'}")
    print()

    # M7 corre contra el fixture del proyecto. Si el servidor no está, se omite con el
    # motivo a la vista: contarla como fallo reprobaría al agente por un puerto apagado.
    fixture = any("127.0.0.1" in m["url"] for m in chosen)
    fixture_ready = fixture_up() if fixture else True
    if fixture and not fixture_ready:
        print("  El fixture del proyecto no responde en 127.0.0.1:8766.")
        print("  Las misiones locales se omiten. Para medirlas: python -m jev_ultrafast.demo\n")

    results = []
    for index, mission in enumerate(chosen, 1):
        print(f"  [{index}/{len(chosen)}] {mission['tag']:<4} {mission['site']:<22} ", end="", flush=True)
        if "127.0.0.1" in mission["url"] and not fixture_ready:
            record = skipped(mission, "el fixture del proyecto no estaba levantado")
        else:
            record = run_mission(mission, timeout_s=args.timeout)
        results.append(record)
        print(f"{record['outcome']:<9} {record['steps']:>3} pasos  {record['elapsed_s']:>6.0f} s  "
              f"{record['tokens']:>7,} tok")
        if record["why"]:
            print(f"         → {record['why'][:110]}")

    forms = [r for r in results if r["form"]]
    card = scorecard(results, forms, expected=len(missions(user, password)))

    print()
    print("=" * 96)
    success = card["success"]

    def mark(evaluable, met, target):
        if not evaluable:
            return "NO EVALUABLE"
        return "CUMPLIDO" if met else "NO CUMPLIDO"

    if not success["evaluable"]:
        why = (f"se corrieron {len(results)} de {success['expected']} misiones"
               + (f" y {success['undecided']} quedaron sin veredicto" if success["undecided"] else ""))
        print(f"  TASA DE ÉXITO   {success['ok']}/{success['total']} = {success['rate'] * 100:.0f} %"
              f"   objetivo ≥ {SUCCESS_TARGET * 100:.0f} %   NO EVALUABLE — {why}")
        print("                   El criterio dice once misiones. Con menos, el porcentaje")
        print("                   no se puede comparar con el objetivo y no se declara.")
    else:
        print(f"  TASA DE ÉXITO   {success['ok']}/{success['total']} = {success['rate'] * 100:.0f} %"
              f"   objetivo ≥ {SUCCESS_TARGET * 100:.0f} %   "
              f"{mark(True, success['met'], SUCCESS_TARGET)}")

    two = card["two_field"]
    parts = []
    if two["skipped_no_credential"]:
        parts.append(f"{two['skipped_no_credential']} sin credencial")
    if two["skipped_environment"]:
        parts.append(f"{two['skipped_environment']} no medibles por entorno")
    tail = f"   (+{', '.join(parts)}, fuera del denominador)" if parts else ""
    print(f"  H11 DOS CAMPOS  {two['filled']}/{two['total']} = {two['rate'] * 100:.0f} %"
          f"   objetivo 100 %   {mark(two['evaluable'], two['met'], TWO_FIELD_TARGET)}{tail}")
    print("=" * 96)
    print()
    print("Lo que este informe NO dice, aunque esté en la tabla:")
    print("  · una misión ÉXITO lo es por su URL final, no porque el agente lo anunciara;")
    print("  · una SIN CLAVE es H11 funcionando: paró en vez de inventar una credencial;")
    print("  · M7 necesita el fixture del proyecto levantado (python -m jev_ultrafast.demo);")
    print("  · con n misiones la tasa tiene un error de ±1/n: 9 % sobre 11 no es un 9 % estable.")
    print()
    print("Uso: python scripts/live_battery.py --model <otro modelo>  para comparar modelos.")

    if args.out:
        path = Path(args.out)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = ROOT / "artifacts" / f"battery-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": policy_description(), "scorecard": card, "results": results,
                "targets": {"success": SUCCESS_TARGET, "two_field": TWO_FIELD_TARGET},
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Informe: {path}")
    if not (success["evaluable"] and two["evaluable"]):
        return 2          # no medible: falta cliente, clave, credencial o misiones
    return 0 if (success["met"] and two["met"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
