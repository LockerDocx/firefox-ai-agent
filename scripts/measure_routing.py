"""H12 · cuánto se ahorra enrutando, medido sobre las misiones reales de la batería.

Replays the eleven goals of the live mission battery (Informe v2, 30/09/2026) through the
task router and counts the planner calls that would not have been paid for. No keys, no
network: the point being measured is a decision made before any model is asked.

    python scripts/measure_routing.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jev_ultrafast import routing  # noqa: E402

# The goals as the battery wrote them. M2 is excluded: it never reached the agent, the site
# served it an anti-bot challenge.
MISSIONS = [
    ("M1", "example.com", "Pulsar el enlace de información y llegar a iana.org"),
    ("M2", "DDG Lite", "Buscar NVIDIA NIM y abrir el primer resultado"),
    ("M3", "Wikipedia ES", "Buscar y abrir el artículo de Yerba mate"),
    ("M4", "saucedemo", "Login estándar, añadir mochila, abrir carrito"),
    ("M4b", "saucedemo guiada", "Login estándar paso a paso, añadir una mochila y abrir el carrito"),
    ("M5", "GitHub", "Abrir la página de Releases del repo"),
    ("M6", "Flights natural", "Vuelo Zúrich-Londres un adulto"),
    ("M6b", "Flights guiada", "Vuelo Zúrich-Londres un adulto, paso a paso"),
    ("M7", "fixture del proyecto", "Design stays en Lisboa con cancelación, abrir Casa Flora"),
    ("M8", "Flights + gpt-oss-20b", "Vuelo Zúrich-Londres un adulto"),
    ("M9", "saucedemo + gpt-oss-20b", "Login estándar, añadir mochila, abrir carrito"),
]

# Medido en la instalación real, no estimado: el ejecutor y el planificador del mismo
#Proveedor, el mismo día, con clave gratuita de NVIDIA.
EXECUTOR_MS = 402
PLANNER_MS = 21_045
# Cada misión puede pedir un plan inicial y hasta MAX_REPLANS replans.
MAX_CALLS_PER_MISSION = 3


def main():
    print("=" * 82)
    print("H12 · enrutamiento por tarea, sobre la batería de misiones reales")
    print("=" * 82)
    print(f"  ejecutor : {EXECUTOR_MS:,} ms por decisión  (medido)")
    print(f"  plan     : {PLANNER_MS:,} ms por llamada    (medido)")
    print(f"  un plan + replans puede llegar a {PLANNER_MS * MAX_CALLS_PER_MISSION / 1000:.0f} s por misión\n")

    saved_calls = 0
    print(f"  {'misión':<6} {'sitio':<22} {'ruta':<8} {'por qué'}")
    print("  " + "-" * 78)
    for tag, site, goal in MISSIONS:
        planned, decision = routing.needs_a_plan(goal, configured="auto")
        if not planned:
            saved_calls += 1
        route = "PLAN" if planned else "DIRECTO"
        print(f"  {tag:<6} {site:<22} {route:<8} {decision['reason'][:44]}")

    total = len(MISSIONS)
    print()
    print("=" * 82)
    print(f"Misiones que se enrutan sin planificador: {saved_calls}/{total} "
          f"({saved_calls / total * 100:.0f} %)")
    saved_ms = saved_calls * PLANNER_MS * MAX_CALLS_PER_MISSION
    print(f"Tiempo de planificador evitado (techo): {saved_ms / 1000:,.0f} s de reloj")
    print(f"Proporción del coste total de una misión: "
          f"{PLANNER_MS * MAX_CALLS_PER_MISSION / (PLANNER_MS * MAX_CALLS_PER_MISSION + 20 * EXECUTOR_MS) * 100:.0f} % "
          f"frente a 20 decisiones de ejecutor")
    print("=" * 82)
    print()
    print("Aviso honesto: esto mide la llamada EVITADA, no el resultado de la misión.")
    print("El criterio de aceptación de H12 (≥ 60 % de éxito) necesita la batería viva con")
    print("clave y navegador reales, y esa sí no se puede medir sin ellos.")
    return saved_calls


if __name__ == "__main__":
    main()
