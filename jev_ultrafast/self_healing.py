"""Componente 3: Selectores Auto-Reparables (Self-Healing Cascades).

Proporciona resolución determinista de elementos en 4 niveles:
  Nivel 1: Accesibilidad ARIA (role + aria-label / name / placeholder)
  Nivel 2: Texto Semántico Visible Normalizado (Coincidencia por palabras clave)
  Nivel 3: Posición Relativa / Jerarquía AXTree
  Nivel 4: Bounding Box (BBox) Coordenadas Visuales Proximidad
"""

import math
import re
from typing import Any, Dict, List, Optional, Tuple


def normalize_string(text: str) -> str:
    """Normaliza cadenas de texto para comparaciones semánticas de etiquetas."""
    text = (text or "").lower().strip()
    return re.sub(r"\s+", " ", text)


def word_tokens(text: str) -> set:
    """Extrae conjunto de palabras significativas para intersección semántica."""
    text = normalize_string(text)
    words = re.findall(r"\w+", text)
    stop = {"de", "del", "el", "la", "los", "las", "en", "para", "por", "un", "una"}
    return {w for w in words if len(w) >= 2 and w not in stop}


class SelfHealingCascadeResolver:
    """Resolutor en cascada de 4 niveles para candidatos del AXTree."""

    @classmethod
    def resolve_candidate(
        cls, candidate_target: Dict[str, Any], current_axtree: List[Dict[str, Any]]
    ) -> Optional[Tuple[Dict[str, Any], int, str]]:
        """Busca el candidato coincidente en el AXTree actual utilizando la cascada de 4 niveles.

        Devuelve (candidato_encontrado, nivel_cascada, nombre_estrategia) o None.
        """
        if not candidate_target or not current_axtree:
            return None

        target_role = normalize_string(candidate_target.get("role", ""))
        target_name = normalize_string(candidate_target.get("name", ""))
        target_bbox = candidate_target.get("bbox") or {}

        # ───────────────────────────────────────────────────────────────────────
        # NIVEL 1: ACCESIBILIDAD ARIA (Match exacto por Rol + Nombre ARIA)
        # ───────────────────────────────────────────────────────────────────────
        if target_name:
            if target_role:
                for cand in current_axtree:
                    c_role = normalize_string(cand.get("role", ""))
                    c_name = normalize_string(cand.get("name", ""))
                    if c_role == target_role and c_name == target_name:
                        return (cand, 1, "aria_role_and_name_exact")

            for cand in current_axtree:
                c_name = normalize_string(cand.get("name", ""))
                if c_name == target_name:
                    return (cand, 1, "aria_name_exact")

        # ───────────────────────────────────────────────────────────────────────
        # NIVEL 2: TEXTO SEMÁNTICO VISIBLE NORMALIZADO (Intersección de palabras clave)
        # ───────────────────────────────────────────────────────────────────────
        if target_name:
            target_words = word_tokens(target_name)
            if target_words:
                best_cand = None
                best_overlap = 0

                for cand in current_axtree:
                    c_name = cand.get("name", "")
                    c_words = word_tokens(c_name)
                    overlap = len(target_words.intersection(c_words))
                    if overlap > best_overlap and overlap >= 2:  # Al menos 2 palabras coinciden
                        best_overlap = overlap
                        best_cand = cand

                if best_cand is not None:
                    return (best_cand, 2, "semantic_text_token_overlap")

        # ───────────────────────────────────────────────────────────────────────
        # NIVEL 3: POSICIÓN RELATIVA / JERARQUÍA AXTREE (Índice Relativo o Rol Contenedor)
        # ───────────────────────────────────────────────────────────────────────
        target_index = candidate_target.get("id")
        if target_index is not None:
            for cand in current_axtree:
                if cand.get("id") == target_index and normalize_string(cand.get("role", "")) == target_role:
                    return (cand, 3, "axtree_index_and_role")

        # ───────────────────────────────────────────────────────────────────────
        # NIVEL 4: COORDENADAS BOUNDING BOX (BBox Proximidad Euclídea)
        # ───────────────────────────────────────────────────────────────────────
        if target_bbox and "x" in target_bbox and "y" in target_bbox:
            tx = float(target_bbox["x"])
            ty = float(target_bbox["y"])

            best_cand = None
            min_dist = float("inf")

            for cand in current_axtree:
                cb = cand.get("bbox")
                if cb and "x" in cb and "y" in cb:
                    cx = float(cb["x"])
                    cy = float(cb["y"])
                    dist = math.hypot(tx - cx, ty - cy)
                    if dist < min_dist and dist <= 50.0:
                        min_dist = dist
                        best_cand = cand

            if best_cand is not None:
                return (best_cand, 4, "bbox_euclidean_proximity")

        return None

    @classmethod
    def generate_cascading_selectors(cls, candidate: Dict[str, Any]) -> List[str]:
        """Genera una lista ordenada de selectores CSS candidatos para la cascada de auto-reparación."""
        selectors = []
        role = normalize_string(candidate.get("role", ""))
        name = candidate.get("name", "").strip()

        if role and name:
            selectors.append(f'[role="{role}"][aria-label="{name}"]')
        if name:
            selectors.append(f'[aria-label="{name}"]')
            selectors.append(f'[placeholder="{name}"]')

        tag = candidate.get("tag") or "button"
        if name:
            selectors.append(f'{tag}[name="{name}"]')

        if candidate.get("element_id"):
            selectors.append(f"#{candidate['element_id']}")

        return selectors
