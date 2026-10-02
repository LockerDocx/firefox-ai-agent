"""H5 · nada que venga del modelo o del host llega al panel como HTML.

La sidebar construye su HTML con plantillas, y la auditoría de v0.13.1 encontró dos
interpolaciones que llegaban a `innerHTML` sin pasar por `escape()`: `h.step` y `h.latency_ms`,
en el bloque del historial. En la práctica las dos son enteros que produce el propio agente
—`len(history)+1` y `round(perf_counter…)`—, así que el riesgo era bajo y el arreglo, de una
línea. Lo que no era bajo era que nada impedia la tercera.

Este fichero hace las dos cosas: las arregla y pone una regla que aguante.

El guard resultó más difícil de escribir que el arreglo, y por dos motivos que conviene dejar
escritos, porque son la forma habitual de escribir un guard que no vigila nada:

  * las plantillas se reparten en varias líneas, así que mirar solo la línea que contiene
    `innerHTML =` no ve nada. Este guard sigue la sentencia hasta su `;`;
  * el escapado ocurre en el *sink*, no en la interpolación. Una plantilla construida dentro
    de `addEntry()` o `trunc()` llega a `escape()` más tarde, y exigir el escape en el punto
    de construirla daría 132 falsos positivos en este fichero.

La regla que sí vale, y es corta: **en lo que se escribe directamente en `innerHTML`, todo
pasa por `escape()`**, con dos escapes que están comprobados y no declarados:

  * un ternario cuyas dos ramas son literales, que no puede emitir otra cosa;
  * una lista corta de valores literales o derivados de un conjunto fijo.

Ninguna de las dos es una vulnerabilidad: ninguna puede llevar texto del modelo, del host o
de la página. Se documentan igual, porque un allowlist sin motivo escrito es un allowlist que
alguien acabará copiando sin mirar.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SIDEBAR = ROOT / "extension" / "sidebar" / "sidebar.js"

# Interpolaciones que llegan a innerHTML sin escape() y por qué es correcto. Cada entrada se
# empareja con su comprobación estructural en la sección 3: la excepción se concede mientras
# siga siendo verdad, y se retira en cuanto deja de serlo. Se listan como expresión exacta y
# no como nombre, porque `kind` a secas taparía también `h.kind`.
LITERAL_ONLY = {
    "dot": "the 🟢/🟡/🔴/⚪ readiness emoji, picked by an if/else chain",
    "lead": "a fixed sentence about the local engine, with <b> in it on purpose",
    "tail": "a fixed sentence about where routing decisions happen",
    "entry.kind": "one of the four labels in KIND_LABELS; addEntry only ever receives a literal",
    "entry.kind.toUpperCase()": "the same fixed label, uppercased for a badge",
    "clock(entry.ts)": "a formatted clock reading: entry.ts is Date.now(), set by this module",
    "seconds": "Math.max(0, Math.round(...)) over a clock difference: a number",
    "level": "the loop variable over the literal array allow/ask/deny",
}

# Qué comprobación estructural sostiene cada excepción. Una entrada nueva sin comprobación
# nueva es una entrada que nadie va a volver a mirar, así que el propio test lo exige.
COVERED = {
    "dot": "locals", "lead": "locals", "tail": "locals",
    "entry.kind": "add_entry", "entry.kind.toUpperCase()": "add_entry",
    "clock(entry.ts)": "timestamp", "seconds": "number", "level": "loop",
}
KIND_OF_CHECK = {"locals", "add_entry", "timestamp", "number", "loop"}

INNER_HTML = re.compile(r"\.innerHTML\s*\+?=")
INTERPOLATION = re.compile(r"\$\{([^{}]*)\}")
# Dos ramas, cada una una cadena literal: elija la que elija, no puede emitir otra cosa.
LITERAL_TERNARY = re.compile(r"^.+? \? '[^']*' : \"[^\"]*\"$")
# Techo de líneas por sentencia, para que una plantilla sin cerrar no se escanee entero.
# Quedarse corto no puede producir un falso negativo en lo que ya se leyó.
STATEMENT_LIMIT = 25


def _lines():
    return SIDEBAR.read_text(encoding="utf-8").splitlines()


def _statements():
    """(línea inicial, texto completo) de cada expresión que escribe en innerHTML."""
    lines = _lines()
    index = 0
    while index < len(lines):
        if not INNER_HTML.search(lines[index]):
            index += 1
            continue
        start = index
        text = lines[index]
        taken = 1
        while not text.rstrip().endswith(";") and taken < STATEMENT_LIMIT:
            index += 1
            if index >= len(lines):
                break
            text += "\n" + lines[index]
            taken += 1
        yield start + 1, text
        index += 1


def _unescaped():
    """Interpolaciones sin escape() en una expresión que escribe directamente en innerHTML."""
    found = []
    for number, text in _statements():
        for expression in INTERPOLATION.findall(text):
            expression = expression.strip()
            if "escape(" in expression or expression in LITERAL_ONLY:
                continue
            if LITERAL_TERNARY.match(expression):
                continue
            found.append((number, expression))
    return found


# ── 1 · el arreglo ──────────────────────────────────────────────────────────────


def test_the_step_and_the_latency_are_escaped():
    """Las dos que la auditoría señaló, por su bloque, para que un arreglo no se esconda."""
    source = SIDEBAR.read_text(encoding="utf-8")
    history_block = source[source.index('class="number"') : source.index('class="time"') + 40]
    assert "escape(String(h.step)" in history_block, "el número de paso sigue sin escapar"
    assert "escape(h.latency_ms)" in history_block, "la latencia sigue sin escapar"


def test_the_text_that_can_actually_come_from_the_model_was_always_escaped():
    """`h.action` y `h.text` son lo que el agente eligió y lo que escribió: lo atacable."""
    source = SIDEBAR.read_text(encoding="utf-8")
    assert "escape(h.action)" in source
    assert "escape(h.text)" in source


# ── 2 · el guard ────────────────────────────────────────────────────────────────


def test_nothing_reaches_inner_html_without_escaping():
    found = _unescaped()
    assert not found, (
        "Interpolaciones sin escape() que llegan a innerHTML: "
        + ", ".join(f"línea {n}: {e}" for n, e in found)
    )


@pytest.mark.parametrize(
    "sabotage",
    [
        ("${escape(h.action)}", "${h.action}"),
        ('${escape(String(h.step).padStart(2, "0"))}', "${h.step}"),
        ("${escape(h.latency_ms)}", "${h.latency_ms}"),
    ],
    ids=["h.action", "h.step", "h.latency_ms"],
)
def test_the_guard_catches_each_of_the_three_it_exists_because_of(sabotage):
    """Un guard que no falla cuando debe fallar no es un guard: aquí se rompe a propósito.

    Se quita el `escape()` de una interpolación y se deja la plantilla bien formada: el
    sabotaje tiene que parecerse a la regresión real, no a un fichero corrupto que el propio
    guard no sabe ni leer. Se prueban las tres, y no solo una, porque las tres estaban en el
    mismo bloque y dos ya estaban arregladas cuando se escribió la tercera.
    """
    original = SIDEBAR.read_text(encoding="utf-8")
    try:
        broken = original.replace(sabotage[0], sabotage[1], 1)
        assert broken != original, f"no se encontró {sabotage[0]} en {SIDEBAR.name}"
        SIDEBAR.write_text(broken, encoding="utf-8")
        assert _unescaped(), f"el guard dejó pasar {sabotage[1]}"
    finally:
        SIDEBAR.write_text(original, encoding="utf-8")


def test_the_guard_does_not_flag_a_literal_ternary():
    """La segunda excepción del guard se sostiene sola, sin lista: dos literales, se acabó."""
    hostile = "state.setup.env_file ? '<b>' + something + '</b>' : '<i>' + other + '</i>'"
    assert not LITERAL_TERNARY.match(hostile), "una rama con contenido no es un literal"
    safe = "scope.level === level ? " + chr(39) + " class=on " + chr(39) + " : " + chr(34) * 2
    assert LITERAL_TERNARY.match(safe)


# ── 3 · las excepciones, comprobadas y no declaradas ────────────────────────────


def test_every_exception_has_a_structural_check():
    assert set(LITERAL_ONLY) == set(COVERED), (
        "excepciones sin comprobación: " + str(sorted(set(LITERAL_ONLY) - set(COVERED)))
        + "; comprobaciones sin excepción: " + str(sorted(set(COVERED) - set(LITERAL_ONLY)))
    )
    assert set(COVERED.values()) <= KIND_OF_CHECK


@pytest.mark.parametrize("name", ["dot", "lead", "tail"])
def test_the_local_exceptions_are_written_out_in_the_file(name):
    """Su valor está en el fichero, así que ningún dato de fuera puede llegar a ellos."""
    declaration = [line for line in _lines() if re.match(rf"\s*(const|let|var)\s+{name}\b", line)]
    assert declaration, f"{name} no está declarado en {SIDEBAR.name}"
    assert "${" not in "\n".join(declaration), f"{name} se construye con una interpolación"
    for line in _lines():
        if re.search(rf"^\s*{name}\s*=(?!=)", line):
            assert "${" not in line, f"{name} se reasigna con una interpolación"


def test_every_entry_carries_one_of_the_four_fixed_labels():
    """`entry.kind` va al class de un div, y de ahí sale el nombre de una clase CSS."""
    source = SIDEBAR.read_text(encoding="utf-8")
    # La declaración de la función también casa con el patrón, y su primer parámetro se
    # llama `kind`: sin filtrarla, el test fallaría por la firma y no por una llamada real.
    calls = re.findall(r"(?<!function )addEntry\(\s*([^,\n]+),", source)
    calls = [call for call in calls if call.strip() != "kind"]
    assert calls, "no se encontró ningún addEntry: el guard se ha quedado sin sujeto que mirar"
    for argument in calls:
        argument = argument.strip()
        # Un literal, o una elección entre dos literales: en los dos casos el conjunto de
        # etiquetas es el de KIND_LABELS y no puede crecer con lo que diga el host.
        literal = argument.startswith(('"', "'"))
        choice = "?" in argument and all(part.strip().startswith(('"', "'"))
                                         for part in argument.split("?")[1].split(":"))
        assert literal or choice, f"addEntry recibe {argument}, que no es un literal"


def test_the_timestamp_is_a_clock_reading_and_not_text():
    assert re.search(r"ts:\s*Date\.now\(\)", SIDEBAR.read_text(encoding="utf-8")), (
        "entry.ts ya no viene de Date.now()"
    )


def test_the_seconds_are_computed_and_not_received():
    assert re.search(r"const seconds\s*=\s*Math\.max\(", SIDEBAR.read_text(encoding="utf-8")), (
        "seconds ya no se calcula: si llegara del host, sería un número que no controlamos"
    )


def test_the_level_loop_walks_a_literal_array():
    """`level` va a un class, y el array es allow/ask/deny escrito en el fichero."""
    source = SIDEBAR.read_text(encoding="utf-8")
    assert re.search(r'\["allow",\s*"ask",\s*"deny"\]', source), "el array de niveles ya no es el de antes"
    assert re.search(r"\.map\(\s*\(level\)", source), "el bucle ya no se llama level"


# ── 4 · escape() hace lo que dice, comprobado en el motor ───────────────────────


def test_escape_neutralises_the_five_characters_that_matter(tmp_path):
    """Ejecutado en node, no leído: un regex puede mentir sobre lo que ejecuta."""
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")
    source = SIDEBAR.read_text(encoding="utf-8")
    body = source[source.index("const escape =") : source.index("const trunc =")]
    # La cadena hostil se pasa con json.dumps y no escrita aquí dentro: anidar comillas de
    # JavaScript dentro de las de Python es la forma más rápida de dejar de probar el
    # escapado y empezar a probar el tecleo.
    probe = "\n".join([
        "const hostile = " + json.dumps("<img src=x onerror=alert(1)> \" ' &") + ";",
        "process.stdout.write(JSON.stringify({",
        "  escaped: escape(hostile),",
        "  empty: escape(undefined),",
        "  zero: escape(0),",
        "  number: escape(402),",
        "  padded: escape(String(7).padStart(2, '0')),",
        "}));",
    ])
    path = tmp_path / "escape.js"
    path.write_text(body + "\n" + probe, encoding="utf-8")
    out = subprocess.run(
        [node, str(path)], capture_output=True, encoding="utf-8", timeout=30, check=True
    ).stdout
    result = json.loads(out)
    assert "<" not in result["escaped"] and ">" not in result["escaped"]
    assert result["escaped"] == "&lt;img src=x onerror=alert(1)&gt; &quot; &#39; &amp;"
    assert result["empty"] == "", "undefined se pinta como texto vacío, no como 'undefined'"
    assert result["zero"] == "0", "escribir 0 es escribir 0, no nada: un paso 0 se vería vacío"
    assert result["number"] == "402"
    assert result["padded"] == "07"


def test_the_sidebar_is_still_valid_javascript():
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado")
    assert subprocess.run(
        [node, "--check", str(SIDEBAR)], capture_output=True, timeout=30
    ).returncode == 0, "sidebar.js no compila: el arreglo de H5 lo ha roto"
