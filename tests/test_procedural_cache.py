"""Tests unitarios para la Caché Procedimental del Sistema 0 (ProceduralCache)."""

import time
from pathlib import Path

from jev_ultrafast.procedural_cache import (
    ProceduralCache,
    RecipeStep,
    compute_fingerprint,
    normalize_intent,
)


def test_normalize_intent():
    assert normalize_intent("Buscar productos en Amazon!") == "buscar productos amazon"
    assert normalize_intent("Iniciar sesión en Gmail") == "iniciar sesión gmail"


def test_compute_fingerprint():
    domain = "amazon.es"
    intent = "Buscar zapatillas para correr"
    candidates = [
        {"id": 1, "role": "searchbox", "name": "Buscar en Amazon.es"},
        {"id": 2, "role": "button", "name": "Ir"},
    ]
    fp1 = compute_fingerprint(domain, intent, candidates)
    fp2 = compute_fingerprint(domain, "buscar zapatillas para correr", candidates)
    assert fp1 == fp2


def test_procedural_cache_save_and_retrieve(tmp_path: Path):
    storage_file = tmp_path / "test_recipes.json"
    cache = ProceduralCache(storage_path=storage_file)

    domain = "google.com"
    intent = "Buscar vuelos de Barcelona a Madrid"
    candidates = [
        {"id": 10, "role": "textbox", "name": "Origen"},
        {"id": 11, "role": "textbox", "name": "Destino"},
        {"id": 12, "role": "button", "name": "Buscar"},
    ]

    steps = [
        RecipeStep(step_id=1, action="type", target={"role": "textbox", "name": "Origen"}, value="Barcelona"),
        RecipeStep(step_id=2, action="type", target={"role": "textbox", "name": "Destino"}, value="Madrid"),
        RecipeStep(step_id=3, action="click", target={"role": "button", "name": "Buscar"}),
    ]

    saved_recipe = cache.save_recipe(domain, intent, candidates, steps)
    assert saved_recipe.recipe_id.startswith("rec_google.com_")
    assert len(saved_recipe.steps) == 3

    t0 = time.perf_counter()
    recipe = cache.get_recipe(domain, intent, candidates)
    latency_ms = (time.perf_counter() - t0) * 1000

    assert recipe is not None
    assert recipe.recipe_id == saved_recipe.recipe_id
    assert latency_ms < 1.0

    cache2 = ProceduralCache(storage_path=storage_file)
    recipe2 = cache2.get_recipe(domain, intent, candidates)
    assert recipe2 is not None
    assert recipe2.recipe_id == saved_recipe.recipe_id


def test_procedural_cache_invalidation(tmp_path: Path):
    storage_file = tmp_path / "test_recipes.json"
    cache = ProceduralCache(storage_path=storage_file)

    domain = "mail.google.com"
    intent = "Iniciar sesión"
    candidates = [{"id": 1, "role": "button", "name": "Siguiente"}]
    steps = [RecipeStep(step_id=1, action="click", target={"role": "button", "name": "Siguiente"})]

    recipe = cache.save_recipe(domain, intent, candidates, steps)
    assert cache.get_recipe(domain, intent, candidates) is not None

    ok = cache.invalidate_recipe(recipe.recipe_id)
    assert ok is True

    assert cache.get_recipe(domain, intent, candidates) is None


def test_match_candidate_step():
    cache = ProceduralCache()
    step = RecipeStep(step_id=1, action="click", target={"role": "button", "name": "Añadir al carrito"})

    candidates = [
        {"id": 1, "role": "link", "name": "Inicio"},
        {"id": 2, "role": "button", "name": "Añadir al carrito"},
        {"id": 3, "role": "button", "name": "Comprar ya"},
    ]

    match = cache.match_candidate_step(step, candidates)
    assert match is not None
    assert match["id"] == 2
