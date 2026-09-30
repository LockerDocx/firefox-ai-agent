"""Componente 2: Gramática GBNF (Grammar-Constrained Decoding).

Define gramáticas libres de contexto (GBNF) para restringir el espacio de muestreo
de tokens durante la inferencia local (ej. llama.cpp / GGUF / ONNX). Garantiza
matemáticamente que la respuesta sea un JSON 100% válido y alineado al esquema.
"""

import json
from typing import Any, Dict, Optional, Set

# Gramática GBNF estándar para selección de candidato y acción de navegador
ACTION_GRAMMAR_GBNF = (
    r'root ::= "{" ws "\"action\":" ws action_type "," ws "\"candidate_id\":" ws candidate_id "," ws '
    r'"\"value\":" ws string "," ws "\"confidence\":" ws confidence_val "," ws "\"reasoning\":" ws string "}"'
    "\n\n"
    'action_type ::= "\"click\"" | "\"type\"" | "\"select\"" | "\"scroll\"" | "\"press_key\"" | '
    '\"\"ask_user\"" | "\"finish\""\n'
    "candidate_id ::= [0-9]+\n"
    'confidence_val ::= ("0." [0-9] [0-9]) | "1.00" | "0.0" | "1.0"\n\n'
    'string ::= "\\"" char* "\\""\n'
    'char ::= [^"\\\\] | "\\\\" [bfrnt"\\\\/] | "\\\\u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]\n'
    "ws ::= [ \\t\\n\\r]*"
)


def build_candidate_constrained_grammar(max_candidate_id: int) -> str:
    """Construye una gramática GBNF dinámica restringiendo candidate_id al rango [1..max_candidate_id]."""
    if max_candidate_id <= 0:
        id_pattern = '"0"'
    elif max_candidate_id < 10:
        options = [f'"{i}"' for i in range(1, max_candidate_id + 1)]
        id_pattern = " | ".join(options)
    else:
        id_pattern = "[1-9] [0-9]*"

    return ACTION_GRAMMAR_GBNF.replace("candidate_id ::= [0-9]+", f"candidate_id ::= {id_pattern}")


class GBNFValidator:
    """Validador y formateador de gramática GBNF para respuestas de acciones."""

    ALLOWED_ACTIONS: Set[str] = {"click", "type", "select", "scroll", "press_key", "ask_user", "finish"}

    @classmethod
    def validate_action_payload(cls, json_str: str, max_candidate_id: Optional[int] = None) -> Dict[str, Any]:
        """Valida que una cadena JSON de respuesta cumpla con la estructura definida por la gramática GBNF.

        Lanza ValueError si viola la gramática.
        """
        text = (json_str or "").strip()
        if not text:
            raise ValueError("Respuesta vacía: no cumple con la gramática GBNF.")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Sintaxis JSON inválida violando GBNF: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("GBNF requiere un objeto JSON principal ({...}).")

        required_keys = {"action", "candidate_id"}
        if not required_keys.issubset(data.keys()):
            raise ValueError(f"Faltan campos obligatorios en el JSON GBNF: {required_keys - data.keys()}")

        action = str(data.get("action", "")).lower().strip()
        if action not in cls.ALLOWED_ACTIONS:
            raise ValueError(f"Acción inválida '{action}'. Acciones permitidas por GBNF: {cls.ALLOWED_ACTIONS}")

        try:
            candidate_id = int(data.get("candidate_id"))
        except (ValueError, TypeError) as e:
            raise ValueError(f"candidate_id debe ser un entero válido: {data.get('candidate_id')}") from e

        in_range = max_candidate_id is not None and max_candidate_id > 0
        if in_range and (candidate_id < 0 or candidate_id > max_candidate_id):
            raise ValueError(
                f"candidate_id {candidate_id} fuera de rango [0..{max_candidate_id}] impuesto por la gramática."
            )

        confidence = float(data.get("confidence", 1.0))
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"confidence {confidence} fuera del rango [0.0..1.0].")

        return {
            "action": action,
            "candidate_id": candidate_id,
            "value": str(data.get("value", "")),
            "confidence": confidence,
            "reasoning": str(data.get("reasoning", "")),
        }
