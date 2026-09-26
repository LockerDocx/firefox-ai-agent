"""Any OpenAI-compatible or Anthropic-compatible endpoint can drive the policy and the text helper.

Roles: "policy" (operation + target choice) and "text" (TYPE_TEXT values). Each role reads
POLICY_* / TEXT_MODEL_* variables, falls back to the provider's own key variable
(OPENROUTER_API_KEY, NVIDIA_API_KEY, ...), and auto-detects the API dialect from the base URL.
"""

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

from . import schemas

PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "dialect": "openai",
        "key_env": ["OPENAI_API_KEY"],
        "json_mode": True,
        "keys_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "dialect": "anthropic",
        "key_env": ["ANTHROPIC_API_KEY"],
        "keys_url": "https://console.anthropic.com/settings/keys",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "dialect": "openai",
        "key_env": ["OPENROUTER_API_KEY"],
        "headers": {"X-Title": "firefox-ai-agent"},
        "keys_url": "https://openrouter.ai/keys",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "dialect": "openai",
        "key_env": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
        "keys_url": "https://build.nvidia.com",
    },
    "omniroute": {
        "base_url": "http://localhost:20128/v1",
        "dialect": "openai",
        "key_env": ["OMNIROUTE_API_KEY"],
        "keys_url": "http://localhost:20128",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "dialect": "openai",
        "key_env": ["DEEPSEEK_API_KEY"],
        "json_mode": True,
        "keys_url": "https://platform.deepseek.com/api_keys",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "dialect": "openai",
        "key_env": ["GROQ_API_KEY"],
        "json_mode": True,
        "keys_url": "https://console.groq.com/keys",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "dialect": "openai",
        "key_env": ["TOGETHER_API_KEY"],
        "json_mode": True,
        "keys_url": "https://api.together.ai/settings/api-keys",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "dialect": "openai",
        "key_env": ["MISTRAL_API_KEY"],
        "json_mode": True,
        "keys_url": "https://console.mistral.ai/api-keys",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "dialect": "openai",
        "key_env": ["XAI_API_KEY"],
        "json_mode": True,
        "keys_url": "https://console.x.ai",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "dialect": "openai",
        "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "json_mode": True,
        "keys_url": "https://aistudio.google.com/apikey",
    },
    "custom": {"dialect": "openai"},
}

ALIASES = {
    "nim": "nvidia",
    "nvidia-nim": "nvidia",
    "nvidia_nim": "nvidia",
    "openai-compatible": "custom",
    "openai_compatible": "custom",
    "compatible": "custom",
    "self-hosted": "custom",
    "grok": "xai",
    "google": "gemini",
    "omni": "omniroute",
}

ROLE_ENV = {
    role: {
        "provider": f"{prefix}_PROVIDER",
        "key": f"{prefix}_API_KEY",
        "base": f"{prefix}_BASE_URL",
        "model": prefix if role == "text" else f"{prefix}_MODEL",
        "json_mode": f"{prefix}_JSON_MODE",
        # one variable per normalized parameter: PLANNER_TEMPERATURE,
        # POLICY_TOP_P, TEXT_MODEL_MAX_TOKENS, ...
        **{name: schemas.env_name(role, name) for name in schemas.BASE_PARAMETERS},
    }
    for role, prefix in schemas.ROLE_ENV_PREFIX.items()
}

POLICY_HINT = (
    "Set TYPESAFE_API_KEY to use Jev, or configure your own provider: "
    "POLICY_PROVIDER (openai, anthropic, openrouter, nvidia, omniroute, deepseek, groq, "
    "together, mistral, xai, gemini, custom) plus POLICY_MODEL and POLICY_API_KEY "
    "(or the provider's own variable, e.g. OPENROUTER_API_KEY)."
)


def repo_root():
    """The checkout this package lives in, or None when it was installed as a package."""
    root = Path(__file__).resolve().parents[1]
    return root if (root / "pyproject.toml").exists() else None


def env_file_path():
    """The one `.env` the agent reads *and* writes, whatever the working directory is.

    The process cwd is not a reliable locator: Firefox chooses it when it launches the
    native host, and a terminal user may run `jev-firefox` from anywhere. Resolving the
    key file relative to the cwd meant the sidebar could save a key in one file while the
    next start read another — the key looked lost ("nothing is configured" with a key
    already saved). One installation, one file:

      1. `JEV_ENV_FILE`, if set (tests, CI, anyone who keeps it elsewhere)
      2. `<checkout>/.env` — the documented location, and where existing keys already are
      3. `./.env`, only when there is no checkout (a package install run from a folder
         that has one)
      4. `~/.config/jev-ultrafast/.env` (a package install with nothing configured yet)
    """
    explicit = (os.environ.get("JEV_ENV_FILE") or "").strip()
    if explicit:
        return Path(explicit)
    root = repo_root()
    if root is not None:
        return root / ".env"
    local = Path.cwd() / ".env"
    if local.exists():
        return local
    return Path.home() / ".config" / "jev-ultrafast" / ".env"


def env_file_display():
    """The path as shown in the sidebar: `~` instead of the user's home directory."""
    path = env_file_path()
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text


def load_env_file(path=None):
    """Read the key file into the environment. One reader for every entry point."""
    target = Path(path) if path else env_file_path()
    if not target.exists():
        return target
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")  # utf-8-sig drops a Notepad BOM
    except OSError:
        return target
    for line in text.splitlines():
        line = line.lstrip("\ufeff")
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]  # Notepad and some editors wrap pasted values in quotes
            os.environ.setdefault(name.strip(), value)
    return target


def detect_preset(base_url):
    url = base_url.rstrip("/")
    for name, preset in PROVIDERS.items():
        preset_url = preset.get("base_url", "").rstrip("/")
        if preset_url and (url == preset_url or url.startswith(preset_url + "/")):
            return name, preset
    return None


def model_capabilities(provider_name, model_id):
    """Catalogue capabilities when the registry has them, inferred from the id otherwise."""
    if not model_id:
        return {}
    try:
        from . import parameters

        capabilities = parameters.capabilities_for(provider_name, model_id)
        if capabilities:
            return capabilities
        from . import discovery

        return discovery._capabilities(model_id)
    except Exception:  # noqa: BLE001 - capability hints must never break a request
        return {}


def model_schema(provider_name, model_id, dialect="openai"):
    """The parameter surface of one model (family rules + capabilities + runtime evidence)."""
    return schemas.schema_for(
        provider_name, model_id, model_capabilities(provider_name, model_id), dialect=dialect
    )


def reasoning_params(setting, name, dialect, model=""):
    """Map the reasoning setting to this model's body params; omit anything unverified.

    The wiring is per family, not per provider: on NVIDIA NIM, Kimi takes
    reasoning_effort low/high/max, GLM and Nemotron toggle thinking through
    chat_template_kwargs, and DeepSeek-R1 always reasons. See jev_ultrafast.schemas.
    """
    if dialect == "anthropic":
        return {}
    wire = model_schema(name, model, dialect).get("reasoning")
    return schemas.reasoning_body(setting, wire, name, dialect)


def _is_loopback_url(base_url):
    host = (urllib.parse.urlparse(base_url or "").hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


# ── zero-config defaults ────────────────────────────────────────────────────
# One free key runs the whole agent: nobody should have to pick three models to
# get started. Quality is not traded away — these are the measured-best
# arrangements of the free tiers (docs/providers.md):
#
#   NVIDIA key present → NVIDIA runs all three roles on z-ai/glm-5.3-flash with thinking off
#     (the user's own choice, 2026-09-26). Measured cost of that choice, same day, same key,
#     scripts/bench_profiles.py: the full-size model answered the executor role in 1.9 s median
#     and the text role in 1.4 s, flash answered 37.4 s and 56.1 s — flash is the smaller model
#     of the same family and waits in the same free-tier queue, which is where the seconds are. The
#     defaults follow the user's instruction; the faster model is one line away for any role.
#     Groq's free tier is 8 000 tokens per minute and the executor burns that in a
#     couple of steps, so a mixed setup started failing mid-run with HTTP 429 and the
#     provider's "upgrade to Dev Tier" message. A free key that expires mid-mission is
#     worse than a slower one, so the mix is opt-in now: set the constants you want.
#   Groq only         → Groq runs all three roles (the fastest measured per step).
#   DeepSeek only     → DeepSeek runs all three.
#
# Measured on NVIDIA, two independent runs of the same prompts (scripts/bench_profiles.py):
#   z-ai/glm-5.3        executor 2.3 s and 5.2 s median (n=12 each), text 1.6 s and 98 s
#   openai/gpt-oss-20b  executor 36 s and 45 s median (n=11-12), worst 180 s
# so the executor and the text writer default to glm-5.3 on that provider: 10x faster on the
# role that is called on every step, at the same measured quality (routing 12/12, 0 dangerous).
DERIVED_MODELS = {
    "nvidia": {
        "planner": "z-ai/glm-5.3-flash",
        "policy": "z-ai/glm-5.3-flash",
        "text": "z-ai/glm-5.3-flash",
    },
    "groq": {"planner": "openai/gpt-oss-120b", "policy": "openai/gpt-oss-20b", "text": "openai/gpt-oss-20b"},
    "deepseek": {"planner": "deepseek-chat", "policy": "deepseek-chat", "text": "deepseek-chat"},
}
DERIVATION_ORDER = ("nvidia", "groq", "deepseek")

# The names the sidebar shows for the same three roles. Kept here (not only in the
# extension) so a message can never call a role something the panel does not show.
ROLE_LABELS = {"planner": "Planner", "policy": "Executor", "text": "Text writer"}

NO_CONFIG_MESSAGE = (
    "{role} has no model yet, so the agent cannot run. One free key covers all three roles: paste it in "
    "the sidebar (NVIDIA_API_KEY, free - https://build.nvidia.com) or set {key} in .env."
)


def key_for(provider_name):
    """The API key available for a provider, looked up in its own variables."""
    preset = PROVIDERS.get(provider_name) or {}
    for variable in preset.get("key_env", []):
        value = (os.environ.get(variable) or "").strip()
        if value:
            return value
    return ""


def derived_provider(role):
    """The provider a role would use when the user configured nothing, or None.

    The first provider in DERIVATION_ORDER that has a key runs *every* role. One key, one
    provider, one failure mode: mixing a second free tier in mid-run is what produced
    HTTP 429s on the executor while the planner was fine.
    """
    for name in DERIVATION_ORDER:
        if key_for(name):
            return name
    return None


def derived_for(role):
    """(provider, model) for a role when nothing was configured, or None."""
    provider_name = derived_provider(role)
    if not provider_name:
        return None
    return provider_name, DERIVED_MODELS[provider_name][role]


def selection_for(role):
    """(provider, model) actually in force for a role: the .env first, derived after.

    One source of truth for resolve(), the sidebar and the planner gate, so what
    the panel shows is what the agent sends.
    """
    env = ROLE_ENV[role]
    raw = (os.environ.get(env["provider"]) or "").strip()
    model = (os.environ.get(env["model"]) or "").strip()
    if raw.startswith(("http://", "https://")):
        provider_name = "custom"
    else:
        provider_name = ALIASES.get(raw.lower(), raw.lower()) if raw else ""
    if not provider_name:
        derived = derived_for(role)
        if not derived:
            return "", ""
        provider_name = derived[0]
        model = model or derived[1]
    elif not model:
        table = DERIVED_MODELS.get(provider_name)
        if table:
            model = table[role]  # a provider on its own implies its documented models
    return provider_name, model


def planner_enabled():
    """True when a planner can run (explicitly configured or derived from a key)."""
    return bool(selection_for("planner")[1])


# ── the only setup step ─────────────────────────────────────────────────────
# The panel shows these in this order: the NVIDIA key first, because one NVIDIA key runs all
# three roles and does not run out mid-mission. Groq is still supported and still the fastest
# per step, but its free tier is 8 000 tokens per minute and the provider's own message is an
# "upgrade to Dev Tier" — reported as a dead run, not as a fast one.
# One key, and it is the only one the panel asks for. Asked, not required: a Groq or DeepSeek
# key still works when it is the one you have, and any mix is available by naming the roles
# yourself — this tuple is about what the agent *offers* on a fresh install, which is the key
# that runs the whole mission without the free tier running out halfway.
FREE_KEYS = (
    ("NVIDIA_API_KEY", "NVIDIA NIM · one key runs all three roles (recommended)", "https://build.nvidia.com"),
)

# Keys the agent used to recommend and no longer does. If one is already on the machine it is
# shown in the panel as detected-and-not-used: an inert key the user cannot see is worse than
# one that is named, with the reason.
OPTIONAL_KEYS = (
    ("GROQ_API_KEY", "Groq", "its free tier is 8 000 tokens/minute and answers 429 mid-mission"),
    ("DEEPSEEK_API_KEY", "DeepSeek", "used only when you name it for a role, or when it is your only key"),
)
NO_KEY_HELP = """
No API key found. One free key runs the whole agent:

  1. Open https://build.nvidia.com, sign in, and open API Keys (free, no card)
  2. Generate one and copy it: it starts with nvapi-
  3. Paste it in the agent sidebar in Firefox (it asks on first open), or here
     when this starter asks, or in .env as NVIDIA_API_KEY=... and run again.

That one key runs the planner, the executor and the text helper (z-ai/glm-5.3).
A Groq key (https://console.groq.com/keys) is optional: faster per call, but its
free tier is 8 000 tokens per minute and a long mission spends that mid-run.
""".strip()


def is_configured():
    """True when the agent has something to run with: a key, or an explicit choice."""
    if (os.environ.get("TYPESAFE_API_KEY") or "").strip():
        return True
    if any(key_for(name) for name in DERIVATION_ORDER):
        return True
    for env in ROLE_ENV.values():
        if (os.environ.get(env["provider"]) or "").strip() or (os.environ.get(env["base"]) or "").strip():
            return True
    return False


def ensure_configured(prompt=input, notify=print, path=None, interactive=None):
    """Ask for a free key when none is configured, and save it to .env.

    This is the whole setup: one key, pasted once. Runs only on an interactive
    terminal, so scripts, CI and tests are never blocked by it. Returns True
    when the agent can run afterwards.
    """
    if is_configured():
        return True
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if not interactive:
        notify(NO_KEY_HELP)
        return False
    notify("")
    notify("The agent needs one free API key. It is stored locally in .env and never leaves your machine.")
    saved = {}
    for variable, label, url in FREE_KEYS:
        notify(f"  {label}: {url}")
        value = ""
        try:
            value = (prompt(f"  Paste {variable} and press Enter (or just Enter to skip): ") or "").strip()
        except (EOFError, KeyboardInterrupt):
            value = ""
        if value:
            os.environ[variable] = value
            saved[variable] = value
    notify("")
    if not saved:
        notify(NO_KEY_HELP)
        return False
    target = _write_env(saved, path)
    notify(f"Saved to {target}. Nothing else to configure.")
    return True


def _write_env(values, path=None):
    """Persist keys into .env, replacing existing lines instead of duplicating them."""
    target = Path(path) if path else env_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)  # first key on a fresh install
    try:
        # utf-8-sig drops the BOM Notepad writes; a stray byte must not stop the agent
        lines = target.read_text(encoding="utf-8-sig", errors="replace").splitlines() if target.exists() else []
    except OSError:
        lines = []
    remaining = dict(values)
    out = []
    for line in lines:
        # A doubled BOM (tools that append to a BOM'd file) must not hide a key.
        line = line.lstrip("\ufeff")
        stripped = line.lstrip()
        name = line.split("=", 1)[0].strip() if "=" in line and not stripped.startswith("#") else ""
        out.append(f"{name}={remaining.pop(name)}" if name in remaining else line)
    out.extend(f"{name}={value}" for name, value in remaining.items())
    try:
        target.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    except OSError:
        pass  # the key still lives in the environment for this run
    return target


SETUP_ROLES = ("planner", "policy", "text")
KEY_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]*_API_KEY\Z")


def save_key(name, value, path=None):
    """Store one API key entered in the sidebar: validate, use now, persist to .env.

    The same .env every other path uses, so the graphical setup and the
    terminal one are equivalent. Exported immediately, which is why no restart
    is needed after pasting. Never logs or returns the value.
    """
    name = (name or "").strip().upper()
    value = (value or "").strip().strip('"').strip("'").strip()
    if not KEY_VARIABLE.match(name):
        raise ValueError("That is not an API-key variable name")
    if not value:
        raise ValueError("No key pasted")
    if any(character.isspace() for character in value) or len(value) > 400:
        raise ValueError("That does not look like an API key (no spaces, no quotes)")
    os.environ[name] = value
    _write_env({name: value}, path=path)
    return name


def setup_status():
    """What the sidebar needs to onboard a new user: which keys exist *by name*.

    Carries booleans and the derived selection, never a key value, so it is safe
    to broadcast on every state update.
    """
    keys = [name for name, _label, _url in FREE_KEYS] + [name for name, _label, _why in OPTIONAL_KEYS]
    return {
        "configured": is_configured(),
        # the sidebar asks "is this variable filled", not "which provider is keyed"
        "keys": {name: bool((os.environ.get(name) or "").strip()) for name in keys},
        "typesafe": bool((os.environ.get("TYPESAFE_API_KEY") or "").strip()),
        "free": [{"variable": name, "label": label, "keys_url": url} for name, label, url in FREE_KEYS],
        # Present but not offered: the panel says so instead of leaving the user wondering why
        # a key they pasted months ago is doing nothing.
        "detected": [
            {"variable": name, "label": label, "note": note}
            for name, label, note in OPTIONAL_KEYS
            if (os.environ.get(name) or "").strip()
        ],
        "selection": {role: list(selection_for(role)) for role in SETUP_ROLES},
        # shown in the sidebar: a key "not found" is usually a key in another file
        "env_file": env_file_display(),
    }


def resolve(role):
    """Build one provider config from the environment for the given role."""
    env = ROLE_ENV[role]
    raw = (os.environ.get(env["provider"]) or "").strip()
    base = (os.environ.get(env["base"]) or "").strip()
    key = (os.environ.get(env["key"]) or "").strip()
    derived = derived_for(role)
    if not raw and not base and not key and derived:
        raw = derived[0]  # nothing configured for this role: use the free key that is present
    if not raw and not base and not key:
        raise ValueError(
            NO_CONFIG_MESSAGE.format(role=ROLE_LABELS.get(role, role), key=env["key"])
        )
    name, preset = "", None
    if raw.startswith(("http://", "https://")):
        base, name, preset = (base or raw), "custom", PROVIDERS["custom"]
        detected = detect_preset(base)
        if detected:  # a raw base URL that matches a known preset, e.g. a self-hosted gateway
            name, preset = detected
    elif raw:
        name = ALIASES.get(raw.lower(), raw.lower())
        preset = PROVIDERS.get(name)
        if preset is None:
            known = ", ".join(sorted(PROVIDERS))
            raise ValueError(f"Unknown provider '{raw}' in {env['provider']}. Use: {known}, or a http(s) base URL.")
    if preset and not base:
        base = preset.get("base_url", "")
    elif not preset and base:
        name, preset = detect_preset(base) or ("", None)
    if not base and role == "text":
        name, preset, base = "deepseek", PROVIDERS["deepseek"], PROVIDERS["deepseek"]["base_url"]
    if not base:
        hint = POLICY_HINT if role == "policy" else ""
        raise ValueError(f"{env['base']} is required for a custom {role} provider. {hint}".strip())
    if not key and preset:
        for key_name in preset.get("key_env", []):
            if (os.environ.get(key_name) or "").strip():
                key = os.environ[key_name].strip()
                break
    if not key and _is_loopback_url(base):
        key = "local"  # a self-hosted gateway on this machine needs no key; the header keeps the path uniform
    if not key:
        if preset and preset.get("key_env"):
            options = " or ".join([env["key"], *preset["key_env"]])
            raise ValueError(
                f"No API key for the {ROLE_LABELS.get(role, role)} role: set {options}. "
                "No request was sent. One free key runs the whole agent - paste it in the "
                "agent sidebar, or NVIDIA_API_KEY in .env (https://build.nvidia.com)."
            )
        raise ValueError(
            f"{env['key']} is not set, and {env['provider']} is not a named provider. "
            "No request was sent. One free key runs the whole agent "
            "(NVIDIA_API_KEY, https://build.nvidia.com)."
        )
    model = (os.environ.get(env["model"]) or "").strip()
    if not model:
        table = DERIVED_MODELS.get(name)
        if table:
            model = table[role]  # the documented model for that provider and role
    if not model:
        hint = POLICY_HINT if role == "policy" else ""
        raise ValueError(f"{env['model']} is not set; no request was sent. {hint}".strip())
    dialect = "anthropic" if "api.anthropic.com" in base else (preset or {}).get("dialect", "openai")
    json_mode = bool((preset or {}).get("json_mode"))
    flag = (os.environ.get(env["json_mode"]) or "").strip().lower()
    if flag in {"on", "true", "1", "yes"}:
        json_mode = True
    elif flag in {"off", "false", "0", "no"}:
        json_mode = False
    provider_name = name or "custom"
    schema = model_schema(provider_name, model, dialect)
    params = {}
    for parameter, definition in schema["parameters"].items():
        raw = (os.environ.get(env[parameter]) or "").strip()
        if not raw:
            continue
        value = schemas.parse_env(parameter, raw, definition)
        if value is None and parameter == "temperature":
            raise ValueError(f"{env['temperature']} must be a number between 0 and 2.")
        if value is not None:
            params[parameter] = value
    if "reasoning" in schema["parameters"]:
        # An unset control still means something: families with an explicit
        # "off" mapping must keep sending it (OpenRouter bills reasoning by default).
        params.setdefault("reasoning", (os.environ.get(env["reasoning"]) or "none").strip().lower())
    return {
        "name": provider_name,
        "dialect": dialect,
        "base_url": base,
        "key": key,
        "model": model,
        "reasoning": params.get("reasoning", (os.environ.get(env["reasoning"]) or "none").strip().lower()),
        "temperature": params.get("temperature"),
        "params": params,
        "schema": schema,
        "json_mode": json_mode,
        "headers": (preset or {}).get("headers", {}),
    }


def _openai_url(base_url):
    url = base_url.rstrip("/")
    return url if url.endswith("/chat/completions") else url + "/chat/completions"


def _anthropic_url(base_url):
    url = base_url.rstrip("/")
    if url.endswith("/messages"):
        return url
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url + "/v1/messages"


MIN_OUTPUT_TOKENS = 64  # a user cap below this would starve the JSON protocol


def build_request(provider, system, user, max_tokens, omit=()):
    """One request body for this exact model: only the parameters it accepts.

    The caller passes the token budget the loop needs; the user's own
    max_tokens control lowers it (never below MIN_OUTPUT_TOKENS) instead of
    replacing it, so a small UI value can slow a run but not break it.
    """
    schema = provider.get("schema") or model_schema(provider["name"], provider["model"], provider["dialect"])
    params = dict(provider.get("params") or {})
    user_cap = params.pop("max_tokens", None)
    if user_cap is not None:
        max_tokens = max(MIN_OUTPUT_TOKENS, min(int(user_cap), max_tokens))
    body = {"model": provider["model"]}
    if "max_tokens" in omit:
        body["max_completion_tokens"] = max_tokens  # newer OpenAI-compatible endpoints
    else:
        body["max_tokens"] = max_tokens
    params.pop("stream", None)  # streaming is decided by the caller, not the body
    body.update(schemas.to_body(params, schema, omit=omit))
    if provider["dialect"] == "anthropic":
        headers = {"x-api-key": provider["key"], "anthropic-version": "2023-06-01"}
        body["system"] = system
        body["messages"] = [{"role": "user", "content": user}]
        return _anthropic_url(provider["base_url"]), headers, body
    if provider["json_mode"] and "response_format" not in omit:
        body["response_format"] = {"type": "json_object"}
    body["messages"] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if provider["headers"]:
        headers = {"Authorization": f"Bearer {provider['key']}", **provider["headers"]}
    else:
        headers = None
    return _openai_url(provider["base_url"]), headers, body


def parse_response(provider, result):
    if provider["dialect"] == "anthropic":
        blocks = result.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    else:
        choices = result.get("choices") or []
        message = (choices[0] or {}).get("message", {}) if choices else {}
        text = message.get("content") or ""
        if isinstance(text, list):  # a few gateways return OpenAI content parts
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    meta = {
        "model": result.get("model") or provider["model"],
        "usage": result.get("usage") or {},
        "provider": provider["name"],
    }
    return text, meta


def chat(provider, system, user, max_tokens=1024, on_delta=None):
    """Send one chat request; adaptively drop params a strict endpoint rejects.

    OpenAI-compatible providers disagree on response_format, reasoning controls,
    temperature, and max_tokens vs max_completion_tokens. Instead of failing,
    retry without the rejected parameter so a model change never breaks a run.

    With on_delta, the reply is streamed over SSE and every text chunk is passed
    to the callback as it arrives; if the endpoint rejects streaming, the call
    falls back to a single request and on_delta simply fires once.
    """
    from . import model  # one shared HTTP seam; tests patch model.post_json / model.post_stream

    dropped = set()
    sent = set(provider.get("params") or {})
    while True:
        url, headers, body = build_request(provider, system, user, max_tokens, omit=dropped)
        if on_delta is not None and "stream" not in dropped:
            body = {**body, "stream": True}
            try:
                text, usage, model_id = model.post_stream(
                    url, provider["key"], body, headers=headers, on_delta=on_delta
                )
                _remember(provider, sent, dropped)
                return text, {"model": model_id or provider["model"], "usage": usage, "provider": provider["name"]}
            except RuntimeError as error:
                drop = _droppable_param(str(error), dropped, provider["dialect"], allow_stream_drop=True)
                if drop is None:
                    raise
                dropped.add(drop)
                continue
        try:
            if headers is None:
                result = model.post_json(url, provider["key"], body)
            else:
                result = model.post_json(url, provider["key"], body, headers=headers)
        except RuntimeError as error:
            drop = _droppable_param(str(error), dropped, provider["dialect"])
            if drop is None:
                raise
            dropped.add(drop)
            continue
        _remember(provider, sent, dropped)
        return parse_response(provider, result)


def _remember(provider, sent, dropped):
    """Turn a completed request into runtime evidence about this model.

    A parameter the endpoint made us drop disappears from the sidebar; the ones
    that survived a successful call are marked verified. This is the runtime
    half of the spec's catalogue-vs-runtime-schema rule.
    """
    name, model_id = provider.get("name", ""), provider.get("model", "")
    for parameter in dropped:
        if parameter in sent:
            schemas.record_unsupported(name, model_id, parameter)
    accepted = [parameter for parameter in sent if parameter not in dropped]
    if accepted:
        schemas.record_verified(name, model_id, accepted)


def _droppable_param(error, dropped, dialect, allow_stream_drop=False):
    """The canonical parameter to drop next for a rejected request, or None."""
    if dialect == "anthropic":
        return None
    lowered = error.lower()
    if allow_stream_drop and "stream" in lowered and "stream" not in dropped:
        return "stream"
    if "response_format" in lowered and "response_format" not in dropped:
        return "response_format"
    # Some endpoints reject the request instead of the parameter: Groq answers a strict
    # JSON schema the model could not satisfy with `json_validate_failed` and an empty
    # generation. The reply is parsed as text anyway, so the schema is what gets dropped.
    if ("json_validate_failed" in lowered or "failed to validate json" in lowered) \
            and "response_format" not in dropped:
        return "response_format"
    if ("reasoning_effort" in lowered or "'reasoning'" in lowered or "chat_template_kwargs" in lowered) \
            and "reasoning" not in dropped:
        return "reasoning"
    # every other normalized parameter, named by the endpoint in its own error
    for parameter in ("top_p", "frequency_penalty", "presence_penalty", "seed", "stop", "temperature"):
        if parameter in lowered and parameter not in dropped:
            return parameter
    if "max_tokens" in lowered and "max_tokens" not in dropped:
        return "max_tokens"
    if "unsupported parameter" in lowered or "unexpected keyword" in lowered:
        for param in ("response_format", "reasoning", "top_p", "seed", "stop",
                      "frequency_penalty", "presence_penalty", "temperature", "max_tokens"):
            if param not in dropped:
                return param
    return None


def extract_json(text):
    """Parse the first JSON object from a model reply, tolerating fences and surrounding prose."""
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Model returned an empty response.")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    start = stripped.find("{")
    if start >= 0:
        depth = 0
        for position, character in enumerate(stripped[start:], start):
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        candidate = json.loads(stripped[start : position + 1])
                    except ValueError:
                        break
                    if isinstance(candidate, dict):
                        return candidate
    raise ValueError("Model response contained no JSON object.")
