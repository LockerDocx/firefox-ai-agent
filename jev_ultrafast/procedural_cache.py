"""Sistema 0: Caché Procedimental de Recetas Compiladas (Procedural Cache).

Proporciona un replay determinista en <1 ms para acciones frecuentes y verificadas,
eliminando completamente la necesidad de inferencia de IA o llamadas a modelos
en tareas repetitivas sobre el mismo sitio web.
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def normalize_intent(intent: str) -> str:
    """Normaliza el texto de intención eliminando puntuación y palabras de relleno."""
    text = (intent or "").lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    stop_words = {"de", "del", "el", "la", "los", "las", "en", "para", "por", "un", "una"}
    words = [w for w in text.split() if w not in stop_words]
    return " ".join(words)


def compute_fingerprint(domain: str, intent: str, axtree_candidates: List[Dict[str, Any]]) -> str:
    """Calcula un hash SHA-256 único a partir del dominio, la intención normalizada

    y la estructura sintética de candidatos del AXTree.
    """
    normalized = normalize_intent(intent)
    ax_signature = []
    for cand in axtree_candidates[:15]:
        role = cand.get("role", "")
        name = (cand.get("name") or "").lower().strip()
        ax_signature.append(f"{role}:{name}")

    ax_str = "|".join(sorted(ax_signature))
    payload = f"{domain.lower()};{normalized};{ax_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecipeStep:
    """Representa un paso individual en una receta compilada."""

    def __init__(
        self,
        step_id: int,
        action: str,
        target: Dict[str, Any],
        value: str = "",
        precondition: Optional[Dict[str, Any]] = None,
        postcondition: Optional[Dict[str, Any]] = None,
    ):
        self.step_id = step_id
        self.action = action
        self.target = target
        self.value = value
        self.precondition = precondition or {"element_visible": True}
        self.postcondition = postcondition or {"url_changed_or_dom_mutated": True}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "target": self.target,
            "value": self.value,
            "precondition": self.precondition,
            "postcondition": self.postcondition,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecipeStep":
        return cls(
            step_id=data.get("step_id", 1),
            action=data.get("action", "click"),
            target=data.get("target", {}),
            value=data.get("value", ""),
            precondition=data.get("precondition"),
            postcondition=data.get("postcondition"),
        )


class CompiledRecipe:
    """Representa una receta procedimental completa validada."""

    def __init__(
        self,
        recipe_id: str,
        domain: str,
        fingerprint_hash: str,
        intent_pattern: str,
        steps: List[RecipeStep],
        success_count: int = 1,
        status: str = "active",
    ):
        self.recipe_id = recipe_id
        self.domain = domain
        self.fingerprint_hash = fingerprint_hash
        self.intent_pattern = intent_pattern
        self.steps = steps
        self.success_count = success_count
        self.status = status
        self.created_at = time.time()
        self.last_used_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "domain": self.domain,
            "fingerprint_hash": self.fingerprint_hash,
            "intent_pattern": self.intent_pattern,
            "steps": [s.to_dict() for s in self.steps],
            "success_count": self.success_count,
            "status": self.status,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompiledRecipe":
        steps = [RecipeStep.from_dict(s) for s in data.get("steps", [])]
        recipe = cls(
            recipe_id=data["recipe_id"],
            domain=data["domain"],
            fingerprint_hash=data["fingerprint_hash"],
            intent_pattern=data.get("intent_pattern", ""),
            steps=steps,
            success_count=data.get("success_count", 1),
            status=data.get("status", "active"),
        )
        recipe.created_at = data.get("created_at", time.time())
        recipe.last_used_at = data.get("last_used_at", time.time())
        return recipe


class ProceduralCache:
    """Gestor principal de la Caché Procedimental del Sistema 0.

    Almacena, busca y convalida recetas procedimentales con latencia <1 ms.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            config_dir = Path.home() / ".config" / "jev-ultrafast"
            config_dir.mkdir(parents=True, exist_ok=True)
            storage_path = config_dir / "procedural_recipes.json"

        self.storage_path = storage_path
        self.recipes: Dict[str, CompiledRecipe] = {}
        self.load()

    def load(self) -> None:
        """Carga las recetas desde el almacenamiento local."""
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for recipe_data in data.get("recipes", []):
                    recipe = CompiledRecipe.from_dict(recipe_data)
                    self.recipes[recipe.recipe_id] = recipe
        except Exception:
            self.recipes = {}

    def save(self) -> None:
        """Guarda las recetas en el almacenamiento local de forma atómica."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "recipes": [r.to_dict() for r in self.recipes.values()],
            }
            temp_path = self.storage_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path.replace(self.storage_path)
        except Exception:
            pass

    def get_recipe(
        self, domain: str, intent: str, axtree_candidates: List[Dict[str, Any]]
    ) -> Optional[CompiledRecipe]:
        """Busca una receta activa que coincida con el dominio, la intención y el fingerprint.

        Latencia garantizada: <1 ms.
        """
        fp_hash = compute_fingerprint(domain, intent, axtree_candidates)

        for recipe in self.recipes.values():
            if recipe.status != "active":
                continue
            if recipe.domain.lower() == domain.lower() and recipe.fingerprint_hash == fp_hash:
                recipe.last_used_at = time.time()
                recipe.success_count += 1
                return recipe

        return None

    def save_recipe(
        self, domain: str, intent: str, axtree_candidates: List[Dict[str, Any]], steps: List[RecipeStep]
    ) -> CompiledRecipe:
        """Compila y guarda una nueva receta procedimental tras la verificación exitosa."""
        fp_hash = compute_fingerprint(domain, intent, axtree_candidates)
        recipe_id = f"rec_{domain}_{fp_hash[:8]}"

        recipe = CompiledRecipe(
            recipe_id=recipe_id,
            domain=domain,
            fingerprint_hash=fp_hash,
            intent_pattern=normalize_intent(intent),
            steps=steps,
            success_count=1,
            status="active",
        )
        self.recipes[recipe_id] = recipe
        self.save()
        return recipe

    def invalidate_recipe(self, recipe_id: str) -> bool:
        """Marca una receta como invalidada tras fallar una postcondición durante el replay."""
        if recipe_id in self.recipes:
            self.recipes[recipe_id].status = "invalidated"
            self.save()
            return True
        return False

    def match_candidate_step(
        self, step: RecipeStep, axtree_candidates: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Empareja deterministamente el objetivo del paso con un candidato del AXTree (<1 ms)."""
        target = step.target
        target_role = (target.get("role") or "").lower().strip()
        target_name = (target.get("name") or "").lower().strip()

        for cand in axtree_candidates:
            c_role = (cand.get("role") or "").lower().strip()
            c_name = (cand.get("name") or "").lower().strip()
            if c_role == target_role and c_name == target_name:
                return cand

        for cand in axtree_candidates:
            c_name = (cand.get("name") or "").lower().strip()
            if c_name and c_name == target_name:
                return cand

        return None
