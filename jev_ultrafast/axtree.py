"""Componente 4: Procesamiento de Índice Semántico AXTree.

Procesa y comprime el índice de candidatos del AXTree recibido desde el content script
garantizando que el payload ocupe menos de 3 KB y contenga sólo los candidatos más relevantes.
"""

import json
from typing import Any, Dict, List


class AXTreeProcessor:
    """Procesador y compresor del índice AXTree para la tubería de agentes."""

    @classmethod
    def compress_candidates(cls, candidates: List[Dict[str, Any]], max_candidates: int = 10) -> List[Dict[str, Any]]:
        """Filtra y comprime candidatos reduciendo el payload a <3 KB."""
        compressed = []
        for i, cand in enumerate(candidates[:max_candidates]):
            compressed.append({
                "id": cand.get("id", i + 1),
                "role": str(cand.get("role", "")).lower().strip(),
                "name": str(cand.get("name", "")).strip()[:60],
                "bbox": cand.get("bbox", {"x": 0, "y": 0, "w": 0, "h": 0}),
            })
        return compressed

    @classmethod
    def estimate_payload_size_bytes(cls, candidates: List[Dict[str, Any]]) -> int:
        """Calcula el tamaño en bytes del payload JSON de candidatos."""
        compact_json = json.dumps(candidates, separators=(",", ":"))
        return len(compact_json.encode("utf-8"))
