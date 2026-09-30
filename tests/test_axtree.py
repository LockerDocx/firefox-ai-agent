"""Tests unitarios para el procesador AXTree (AXTreeProcessor)."""

from jev_ultrafast.axtree import AXTreeProcessor


def test_compress_candidates():
    raw_candidates = [
        {
            "id": i + 1,
            "role": "button",
            "name": f"Botón número {i + 1} con texto largo",
            "bbox": {"x": 10, "y": 20, "w": 100, "h": 40},
        }
        for i in range(20)
    ]

    compressed = AXTreeProcessor.compress_candidates(raw_candidates, max_candidates=8)
    assert len(compressed) == 8
    assert compressed[0]["id"] == 1
    assert "Botón número 1" in compressed[0]["name"]


def test_estimate_payload_size_bytes():
    candidates = [
        {"id": 1, "role": "searchbox", "name": "Buscar zapatillas", "bbox": {"x": 100, "y": 200, "w": 300, "h": 40}},
        {"id": 2, "role": "button", "name": "Buscar", "bbox": {"x": 410, "y": 200, "w": 80, "h": 40}},
    ]

    size_bytes = AXTreeProcessor.estimate_payload_size_bytes(candidates)
    assert size_bytes < 3000  # <3 KB garantizado
