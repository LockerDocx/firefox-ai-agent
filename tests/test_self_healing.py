"""Tests unitarios para la resolución de Selectores Auto-Reparables (SelfHealingCascadeResolver)."""

from jev_ultrafast.self_healing import SelfHealingCascadeResolver


def test_resolve_level_1_aria_role_and_name():
    target = {"role": "button", "name": "Añadir al carrito", "bbox": {"x": 100, "y": 200}}
    axtree = [
        {"id": 1, "role": "link", "name": "Inicio"},
        {"id": 2, "role": "button", "name": "Añadir al carrito", "bbox": {"x": 105, "y": 205}},
    ]

    res = SelfHealingCascadeResolver.resolve_candidate(target, axtree)
    assert res is not None
    cand, level, strategy = res
    assert cand["id"] == 2
    assert level == 1
    assert strategy == "aria_role_and_name_exact"


def test_resolve_level_2_semantic_substring():
    # Nombre ligeramente modificado en la web
    target = {"role": "button", "name": "Buscar en la tienda", "bbox": {"x": 50, "y": 50}}
    axtree = [
        {"id": 1, "role": "button", "name": "Buscar productos en la tienda online"},
    ]

    res = SelfHealingCascadeResolver.resolve_candidate(target, axtree)
    assert res is not None
    cand, level, strategy = res
    assert cand["id"] == 1
    assert level == 2
    assert strategy == "semantic_text_token_overlap"


def test_resolve_level_3_axtree_index():
    target = {"id": 5, "role": "textbox", "name": "", "bbox": {"x": 0, "y": 0}}
    axtree = [
        {"id": 5, "role": "textbox", "name": "Campo de Texto Modificado"},
    ]

    res = SelfHealingCascadeResolver.resolve_candidate(target, axtree)
    assert res is not None
    cand, level, strategy = res
    assert cand["id"] == 5
    assert level == 3
    assert strategy == "axtree_index_and_role"


def test_resolve_level_4_bbox_proximity():
    # El nombre y el rol cambiaron por completo, pero las coordenadas BBox son casi idénticas (proximidad <50px)
    target = {"role": "div", "name": "Icono Desconocido", "bbox": {"x": 300, "y": 400}}
    axtree = [
        {"id": 9, "role": "span", "name": "Nuevo Icono", "bbox": {"x": 302, "y": 405}},
    ]

    res = SelfHealingCascadeResolver.resolve_candidate(target, axtree)
    assert res is not None
    cand, level, strategy = res
    assert cand["id"] == 9
    assert level == 4
    assert strategy == "bbox_euclidean_proximity"


def test_generate_cascading_selectors():
    candidate = {"role": "button", "name": "Comprar", "tag": "button", "element_id": "btn-buy-now"}
    selectors = SelfHealingCascadeResolver.generate_cascading_selectors(candidate)

    assert '[role="button"][aria-label="Comprar"]' in selectors
    assert '[aria-label="Comprar"]' in selectors
    assert '#btn-buy-now' in selectors
