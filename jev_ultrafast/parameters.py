"""Normalized parameter schema, presets, and persisted model selection.

Models are data, not branches: the sidebar renders its controls from the
schema this module hands it, and that schema is *per model* — the NVIDIA NIM
catalogue changes weekly and Kimi, GLM, gpt-oss or an Anthropic endpoint each
accept a different set of fields (see :mod:`jev_ultrafast.schemas`).

- :data:`PARAMETER_SCHEMA` is the full vocabulary (every role, every parameter).
- :func:`role_schema` narrows it to what the model currently selected for that
  role accepts, using family rules, catalogue capabilities and runtime evidence.
- Presets express intent ("Deep") and are projected onto whatever the chosen
  model actually supports, so switching models never writes an invalid value.

The user's selection is persisted to artifacts/model-config.json and re-applied
over .env at startup.
"""

import json
import os
import re
import time
from pathlib import Path

from . import schemas

CONFIG_PATH = Path(os.environ.get("JEV_MODEL_CONFIG", "artifacts/model-config.json"))

ROLES = [
    {"key": "planner", "label": "Planner", "hint": "Decomposes the mission once"},
    {"key": "policy", "label": "Executor", "hint": "Picks each action"},
    {"key": "text", "label": "Text writer", "hint": "Fills text fields"},
]

# The whole vocabulary. What a given model accepts is a subset, resolved by
# role_schema(); the sidebar renders that subset, never this one directly.
PARAMETER_SCHEMA = {
    "roles": ROLES,
    "parameters": schemas.BASE_PARAMETERS,
    "tiers": ["simple", "advanced"],
}

PRESETS = {
    "fast": {
        "label": "Fast",
        "description": "Lowest latency; minimal thinking",
        "params": {
            "planner": {"reasoning": "low"},
            "policy": {"reasoning": "low"},
            "text": {"reasoning": "low"},
        },
    },
    "balanced": {
        "label": "Balanced",
        "description": "General-purpose defaults",
        "params": {
            "planner": {"reasoning": "medium"},
            "policy": {"reasoning": "low"},
            "text": {"reasoning": "low"},
        },
    },
    "deep": {
        "label": "Deep",
        "description": "Heavy reasoning for complex research or planning",
        "params": {
            "planner": {"reasoning": "high"},
            "policy": {"reasoning": "medium"},
        },
    },
    "browser": {
        "label": "Browser",
        "description": "Conservative, fast browser interaction",
        "params": {
            "planner": {"reasoning": "low"},
            "policy": {"reasoning": "low", "temperature": 0.1},
            "text": {"reasoning": "low", "temperature": 0.2},
        },
    },
    "coding": {
        "label": "Coding",
        "description": "Precise output for code and files",
        "params": {
            "planner": {"reasoning": "high"},
            "policy": {"reasoning": "medium", "temperature": 0.2, "top_p": 0.9},
            "text": {"temperature": 0.2},
        },
    },
    "deterministic": {
        "label": "Deterministic",
        "description": "Same seed, no creativity — for reproducible runs",
        "params": {
            "planner": {"temperature": 0, "seed": 7},
            "policy": {"temperature": 0, "seed": 7},
            "text": {"temperature": 0, "seed": 7},
        },
    },
}

# (role, parameter) → environment variable, for every parameter in the vocabulary.
ROLE_PARAM_ENV = {
    (role["key"], name): schemas.env_name(role["key"], name)
    for role in ROLES
    for name in schemas.BASE_PARAMETERS
}

ROLE_MODEL_ENV = {
    "planner": ("PLANNER_PROVIDER", "PLANNER_MODEL"),
    "text": ("TEXT_MODEL_PROVIDER", "TEXT_MODEL"),
    "policy": ("POLICY_PROVIDER", "POLICY_MODEL"),
}
ROLE_MODEL_ENV = {role["key"]: ROLE_MODEL_ENV[role["key"]] for role in ROLES}  # keep role order


def _role_keys():
    return {role["key"] for role in ROLES}


def model_for(role):
    """(provider, model_id) in force for a role: the .env first, derived after.

    The derivation (zero-config start) lives in providers.selection_for so the
    panel, the planner gate and the request builder can never disagree.
    """
    from . import providers

    return providers.selection_for(role)


def capabilities_for(provider_name, model_id):
    """Catalogue capabilities for a model, from the cached registry when present."""
    if not provider_name or not model_id:
        return {}
    try:
        from . import discovery

        registry = discovery._load_registry() or {}
    except Exception:  # noqa: BLE001 - the registry is a cache, never a dependency
        return {}
    report = (registry.get("providers") or {}).get(provider_name) or {}
    for entry in report.get("models") or []:
        if entry.get("id") == model_id:
            return entry.get("capabilities") or {}
    return {}


def role_schema(role):
    """The parameter surface of the model currently selected for this role."""
    if role not in _role_keys():
        raise ValueError(f"Unknown role: {role}")
    from . import providers

    provider_name, model_id = model_for(role)
    dialect = providers.PROVIDERS.get(provider_name, {}).get("dialect", "openai")
    if not model_id:
        # Nothing selected yet: offer the whole vocabulary so a value can be
        # configured ahead of the model, and mark the surface as unresolved.
        schema = schemas.schema_for(provider_name, "", {"reasoning": True}, dialect=dialect)
        schema["parameters"] = {
            name: schema["parameters"].get(name, definition)
            for name, definition in schemas.BASE_PARAMETERS.items()
        }
        schema["resolved"] = False
        return schema
    schema = schemas.schema_for(
        provider_name, model_id, capabilities_for(provider_name, model_id), dialect=dialect
    )
    schema["resolved"] = True
    return schema


def _validate(role, params, schema=None):
    if role not in _role_keys():
        raise ValueError(f"Unknown role: {role}")
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    schema = schema or role_schema(role)
    supported = schema.get("parameters", {})
    cleaned = {}
    for name, value in params.items():
        if name not in schemas.BASE_PARAMETERS:
            raise ValueError(f"Unknown parameter: {name}")
        if name not in supported:
            model_id = schema.get("model") or "the selected model"
            raise ValueError(f"{name} is not supported by {model_id}")
        cleaned[name] = schemas.coerce(name, value, supported[name])
    return cleaned


def supported_subset(role, params):
    """The part of a parameter set this role's model accepts, silently dropping the rest."""
    schema = role_schema(role)
    supported = schema.get("parameters", {})
    kept = {}
    for name, value in (params or {}).items():
        if name not in supported:
            continue
        try:
            kept[name] = schemas.coerce(name, value, supported[name])
        except ValueError:
            if name == "reasoning":  # e.g. "medium" on a low/high/max family
                nearest = schemas._closest(str(value), supported[name]["values"])
                if nearest:
                    kept[name] = nearest
    return kept


def apply_params(role, params):
    """Validate and apply one role's parameters to the environment + config file."""
    cleaned = _validate(role, params)
    config = load_config()
    stored = config.setdefault("params", {}).setdefault(role, {})
    for name, value in cleaned.items():
        env_var = ROLE_PARAM_ENV[(role, name)]
        if value is None:
            os.environ.pop(env_var, None)
            stored.pop(name, None)
        else:
            os.environ[env_var] = schemas.format_env(value)
            stored[name] = value
    save_config(config)
    return cleaned


def apply_preset(preset_key):
    """Apply a preset, projected onto what each role's model actually supports."""
    if preset_key not in PRESETS:
        raise ValueError(f"Unknown preset: {preset_key}")
    applied = {}
    for role, params in PRESETS[preset_key]["params"].items():
        if role not in _role_keys():
            continue
        wanted = supported_subset(role, params)
        applied[role] = apply_params(role, wanted) if wanted else {}
    config = load_config()
    config["preset"] = preset_key
    save_config(config)
    return applied


def prune_params(role):
    """Drop stored values the role's current model does not accept.

    Called after a model switch: moving from GLM to Kimi must not keep sending
    top_p, and the sidebar must stop showing a control that no longer exists.
    """
    schema = role_schema(role)
    supported = schema.get("parameters", {})
    config = load_config()
    stored = (config.get("params") or {}).get(role) or {}
    removed = []
    for name in list(stored):
        if name in supported:
            try:
                stored[name] = schemas.coerce(name, stored[name], supported[name])
            except ValueError:
                pass
            else:
                os.environ[ROLE_PARAM_ENV[(role, name)]] = schemas.format_env(stored[name])
                continue
        removed.append(name)
        stored.pop(name, None)
        os.environ.pop(ROLE_PARAM_ENV[(role, name)], None)
    # a value that never reached the config file can still sit in the environment
    for name in schemas.BASE_PARAMETERS:
        if name not in supported and os.environ.get(ROLE_PARAM_ENV[(role, name)]):
            os.environ.pop(ROLE_PARAM_ENV[(role, name)], None)
            if name not in removed:
                removed.append(name)
    if removed:
        config.setdefault("params", {})[role] = stored
        save_config(config)
    return removed


def apply_model(role, provider_name, model_id):
    """Switch one role's provider+model in the environment and config file."""
    if role not in ROLE_MODEL_ENV:
        raise ValueError(f"Unknown role: {role}")
    model_id = model_id.strip()
    if not model_id or any(character.isspace() for character in model_id):
        raise ValueError("Invalid model id")  # colons are fine: some catalogues tag their models
    provider_env, model_env = ROLE_MODEL_ENV[role]
    os.environ[provider_env] = provider_name
    os.environ[model_env] = model_id.strip()
    config = load_config()
    config.setdefault("models", {})[role] = {"provider": provider_name, "model": model_id.strip()}
    save_config(config)
    return prune_params(role)


def save_profile(name):
    """Snapshot the current models + parameters as a named profile."""
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise ValueError("Profile name must be 1-40 characters")
    selection = current_selection()
    config = load_config()
    config.setdefault("profiles", {})[name] = {
        "savedAt": time.time(),
        "models": {
            role: {"provider": data["provider"], "model": data["model"]}
            for role, data in selection.items()
            if data.get("provider") and data.get("model")
        },
        "params": {role: data.get("params") or {} for role, data in selection.items()},
    }
    save_config(config)
    return config["profiles"][name]


def apply_profile(name):
    """Restore a saved profile into the environment and the config file."""
    profile = (load_config().get("profiles") or {}).get((name or "").strip())
    if not profile:
        raise ValueError(f"Unknown profile: {name}")
    for role, selection in (profile.get("models") or {}).items():
        if role in ROLE_MODEL_ENV and selection.get("provider") and selection.get("model"):
            apply_model(role, selection["provider"], selection["model"])
    for role, params in (profile.get("params") or {}).items():
        if role in ROLE_MODEL_ENV and isinstance(params, dict):
            kept = supported_subset(role, {k: v for k, v in params.items() if v is not None})
            if kept:
                apply_params(role, kept)
    return profile


def delete_profile(name):
    config = load_config()
    profiles = config.get("profiles") or {}
    if (name or "").strip() not in profiles:
        raise ValueError(f"Unknown profile: {name}")
    del profiles[(name or "").strip()]
    save_config(config)


def profile_names():
    return sorted((load_config().get("profiles") or {}).keys())


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


# The model that used to be derived for every cloud role, and the one that replaced it on
# 2026-09-27. A saved selection still pointing at the old one is moved once, with the reason
# kept in the file; JEV_KEEP_MODEL=1 leaves it alone for anyone who wants it back.
OLD_DEFAULT_MODEL = "z-ai/glm-5.3"
NEW_DEFAULT_MODEL = "z-ai/glm-5.3-flash"


def migrate_old_default_model(config=None):
    """Move a saved selection still on the old default to the shipped one. Returns the roles moved.

    Only the exact old id, and only for NVIDIA: anything else a user typed is their choice and is
    left untouched. The panel can put it back in two clicks, and `JEV_KEEP_MODEL=1` skips this.
    """
    if (os.environ.get("JEV_KEEP_MODEL") or "").strip().lower() in {"1", "on", "yes", "true"}:
        return []
    config = load_config() if config is None else config
    moved = []
    for role, selection in (config.get("models") or {}).items():
        if not isinstance(selection, dict):
            continue
        if selection.get("provider") == "nvidia" and selection.get("model") == OLD_DEFAULT_MODEL:
            selection["model"] = NEW_DEFAULT_MODEL
            moved.append(role)
    if moved:
        config["models_migrated_from"] = OLD_DEFAULT_MODEL
        save_config(config)
    return sorted(moved)


def apply_saved_config():
    """Re-apply the persisted model/parameter selection over the .env defaults."""
    config = load_config()
    if migrate_old_default_model(config):
        config = load_config()  # the migration rewrote the file; apply what it left
    for role, selection in (config.get("models") or {}).items():
        if role in ROLE_MODEL_ENV and isinstance(selection, dict):
            provider_name = selection.get("provider")
            model_id = selection.get("model")
            if provider_name and model_id:
                provider_env, model_env = ROLE_MODEL_ENV[role]
                os.environ[provider_env] = provider_name
                os.environ[model_env] = model_id
    for role, params in (config.get("params") or {}).items():
        if role in ROLE_MODEL_ENV and isinstance(params, dict):
            for name, value in params.items():
                env_var = ROLE_PARAM_ENV.get((role, name))
                if env_var and value is not None:
                    os.environ[env_var] = schemas.format_env(value)
    return config


def role_params(role, schema=None):
    """One role's effective parameter values, read back from the environment."""
    schema = schema or role_schema(role)
    supported = schema.get("parameters", {})
    values = {}
    for name, definition in supported.items():
        raw = os.environ.get(ROLE_PARAM_ENV[(role, name)], "")
        if raw == "":
            continue
        value = schemas.parse_env(name, raw, definition)
        if value is not None:
            values[name] = value
    return values


def current_selection():
    """What each role resolves to right now, for the sidebar."""
    from . import providers

    selection = {}
    for role in ROLE_MODEL_ENV:
        provider_name, model = model_for(role)
        schema = role_schema(role)
        selection[role] = {
            "provider": provider_name,
            "model": model,
            "params": role_params(role, schema),
            "schema": schema,
            "display": _display_name(model),
            "dialect": providers.PROVIDERS.get(provider_name, {}).get("dialect", "openai"),
        }
    return selection


def _display_name(model_id):
    if not model_id:
        return "—"
    tail = model_id.rsplit("/", 1)[-1].replace("_", "-")
    words = re.split(r"-", tail)  # keep dots: "glm-5.3" → "GLM 5.3"
    acronyms = {"glm", "gpt", "llama", "qwen", "kimi", "oss", "ai", "vl", "nemotron", "devstral"}
    return " ".join(w.upper() if w.lower() in acronyms else (w[:1].upper() + w[1:]) for w in words if w)
