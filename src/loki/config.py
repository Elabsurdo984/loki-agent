import json
import os
from pathlib import Path
from typing import Any
import yaml

DEFAULT_MODEL = "gemini/gemini-flash-latest"
MODELS_REGISTRY_PATH = Path(".loki/models.json")


def load_loki_config() -> dict:
    """Reads project configuration from .loki/config.yaml if available."""
    config_file = Path(".loki/config.yaml")
    if config_file.exists():
        try:
            with open(config_file, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}


# --- Model registry (.loki/models.json) -------------------------------------
#
# A small, chat-editable list of named AI connection profiles (`loki chat`'s
# `/model` command reads and writes this file). Whichever profile is marked
# `active` here takes priority over .loki/config.yaml's `ai:` section for
# EVERY LOKI feature that talks to an LLM (chat, rules evaluation, fix,
# auto-heal) — not just chat — since they all resolve through this module.
# An explicit --model flag still wins over both.

def load_models_registry() -> dict[str, Any]:
    """Reads the model profile registry, or an empty one if it doesn't exist yet."""
    if MODELS_REGISTRY_PATH.exists():
        try:
            with open(MODELS_REGISTRY_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("active", None)
                    data.setdefault("profiles", [])
                    return data
        except Exception:
            pass
    return {"active": None, "profiles": []}


def save_models_registry(registry: dict[str, Any]) -> None:
    MODELS_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODELS_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


def list_model_profiles() -> list[dict[str, Any]]:
    return load_models_registry().get("profiles", [])


def get_model_profile(name: str) -> dict[str, Any] | None:
    name_lower = name.lower()
    for profile in list_model_profiles():
        if str(profile.get("name", "")).lower() == name_lower:
            return profile
    return None


def get_active_model_profile() -> dict[str, Any] | None:
    registry = load_models_registry()
    active_name = registry.get("active")
    if not active_name:
        return None
    return get_model_profile(active_name)


def add_model_profile(
    name: str, model: str, api_base: str | None = None, api_key_env: str | None = None
) -> dict[str, Any]:
    """Adds (or updates) a named profile and returns it. Does not activate it."""
    registry = load_models_registry()
    profile: dict[str, Any] = {"name": name, "model": model}
    if api_base:
        profile["api_base"] = api_base
    if api_key_env:
        profile["api_key_env"] = api_key_env
    name_lower = name.lower()
    registry["profiles"] = [
        p for p in registry.get("profiles", []) if str(p.get("name", "")).lower() != name_lower
    ] + [profile]
    save_models_registry(registry)
    return profile


def remove_model_profile(name: str) -> bool:
    """Removes a profile by name. Clears `active` too if it pointed at it. Returns whether it existed."""
    registry = load_models_registry()
    profiles = registry.get("profiles", [])
    name_lower = name.lower()
    remaining = [p for p in profiles if str(p.get("name", "")).lower() != name_lower]
    existed = len(remaining) != len(profiles)
    registry["profiles"] = remaining
    if str(registry.get("active") or "").lower() == name_lower:
        registry["active"] = None
    if existed:
        save_models_registry(registry)
    return existed


def activate_model_profile(name: str) -> dict[str, Any] | None:
    """Marks an existing profile as active. Returns it, or None if no such profile exists."""
    profile = get_model_profile(name)
    if not profile:
        return None
    registry = load_models_registry()
    registry["active"] = profile["name"]  # canonical stored casing, not whatever the caller typed
    save_models_registry(registry)
    return profile


def clear_active_model_profile() -> None:
    """Reverts to whatever .loki/config.yaml's `ai:` section (or the bundled default) says."""
    registry = load_models_registry()
    if registry.get("active"):
        registry["active"] = None
        save_models_registry(registry)


# --- Resolution: explicit arg > active registry profile > config.yaml > default

def resolve_model(explicit: str | None = None) -> str:
    """Returns a LiteLLM model id: explicit value, else the active `/model` profile
    (.loki/models.json), else .loki/config.yaml's `ai:` section, else the default."""
    if explicit:
        return explicit
    active = get_active_model_profile()
    if active:
        return active["model"]
    ai = load_loki_config().get("ai") or {}
    model = ai.get("model")
    if not model:
        return DEFAULT_MODEL
    if "/" not in model and ai.get("provider"):
        return f"{ai['provider']}/{model}"
    return model


def model_source(explicit: str | None = None) -> str:
    """Human-readable description of where the resolved model is coming from."""
    if explicit:
        return "--model flag"
    active = get_active_model_profile()
    if active:
        return f"/model profile '{active['name']}'"
    ai = load_loki_config().get("ai") or {}
    if ai.get("model"):
        return ".loki/config.yaml"
    return "bundled default"


def is_ai_customized(explicit_model: str | None = None) -> bool:
    """True once the caller, the active `/model` profile, or .loki/config.yaml points
    at anything other than LOKI's bundled Gemini default. When true, LOKI must try
    exactly what was configured and never silently swap in a different provider's model."""
    if explicit_model or get_active_model_profile():
        return True
    ai = load_loki_config().get("ai") or {}
    return bool(ai.get("model") or ai.get("api_base") or ai.get("api_key_env"))


def resolve_ai_connection(explicit_model: str | None = None) -> dict[str, Any]:
    """Resolves everything needed to call ANY LiteLLM-compatible AI provider — not
    just Gemini/OpenAI/Anthropic — including a fully custom or self-hosted
    OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, an internal gateway,
    OpenRouter, Groq, Mistral, Azure, ...). Priority: explicit arg > the active
    `/model` profile (.loki/models.json, editable from `loki chat`) > .loki/config.yaml's
    `ai:` section:

        ai:
          provider: mistral             # optional: prefixes `model` if it has no "/"
          model: mistral-large-latest   # any LiteLLM model id: "provider/model", or a
                                         # bare name when using a custom api_base
          api_base: https://host/v1     # optional: point at any self-hosted or
                                         # OpenAI-compatible server
          api_key_env: MY_PROVIDER_KEY  # optional: env var holding the key, when it
                                         # doesn't match the provider's default name

    Returns kwargs ready to splat into litellm.completion(**kwargs, messages=...).
    """
    if explicit_model:
        # An explicit override (--model) gets a clean connection: just that model,
        # nothing else. .loki/config.yaml's api_base/api_key_env belong to ITS OWN
        # `model` entry — blindly inheriting them here would silently route an
        # unrelated explicit model (and its provider's key) through whatever
        # custom endpoint/key was configured for a different model entirely.
        return {"model": explicit_model}

    active = get_active_model_profile()
    if active:
        kwargs: dict[str, Any] = {"model": active["model"]}
        if active.get("api_base"):
            kwargs["api_base"] = active["api_base"]
        if active.get("api_key_env"):
            api_key = os.environ.get(active["api_key_env"])
            if api_key:
                kwargs["api_key"] = api_key
        return kwargs

    ai = load_loki_config().get("ai") or {}
    kwargs = {"model": resolve_model(None)}

    api_base = ai.get("api_base")
    if api_base:
        kwargs["api_base"] = api_base

    api_key_env = ai.get("api_key_env")
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if api_key:
            kwargs["api_key"] = api_key

    return kwargs


def resolve_api_chaos_config(
    cli_enabled: bool | None = None,
    fault_rate: float | None = None,
    auth_chaos: bool | None = None,
    auth_fault_rate: float | None = None,
) -> Any:
    """
    Builds an ApiChaosConfig by overlaying CLI arguments on top of .loki/config.yaml
    `api_chaos:` section and sensible defaults.
    """
    from src.loki.engine.api_chaos import ApiChaosConfig

    yaml_cfg = load_loki_config().get("api_chaos") or {}

    # Enabled resolution: CLI flag > yaml config > default (True)
    if cli_enabled is not None:
        enabled = cli_enabled
    elif "enabled" in yaml_cfg:
        enabled = bool(yaml_cfg["enabled"])
    else:
        enabled = True

    # Fault rate resolution: CLI flag > yaml config > default (0.3)
    if fault_rate is not None:
        rate = fault_rate
    elif "fault_rate" in yaml_cfg:
        rate = float(yaml_cfg["fault_rate"])
    else:
        rate = 0.3

    # Auth chaos resolution: CLI flag > yaml config > default (True)
    if auth_chaos is not None:
        auth_enabled = auth_chaos
    elif "auth_chaos" in yaml_cfg:
        auth_enabled = bool(yaml_cfg["auth_chaos"])
    elif "auth_chaos_enabled" in yaml_cfg:
        auth_enabled = bool(yaml_cfg["auth_chaos_enabled"])
    else:
        auth_enabled = True

    # Auth fault rate resolution: CLI flag > yaml config > default (0.4)
    if auth_fault_rate is not None:
        auth_rate = auth_fault_rate
    elif "auth_fault_rate" in yaml_cfg:
        auth_rate = float(yaml_cfg["auth_fault_rate"])
    else:
        auth_rate = 0.4

    if "fault_types" in yaml_cfg and yaml_cfg["fault_types"] is not None:
        fault_types = list(yaml_cfg["fault_types"])
    else:
        fault_types = [
            "status_code",
            "corrupt_json",
            "delay",
            "empty_response",
            "schema_strip",
        ]

    if "status_codes" in yaml_cfg and yaml_cfg["status_codes"] is not None:
        status_codes = [int(sc) for sc in yaml_cfg["status_codes"]]
    else:
        status_codes = [500, 502, 503, 504]

    if "delay_range_ms" in yaml_cfg and yaml_cfg["delay_range_ms"] is not None:
        raw_delay = yaml_cfg["delay_range_ms"]
        delay_range_ms = (int(raw_delay[0]), int(raw_delay[1]))
    else:
        delay_range_ms = (1500, 3500)

    if "api_patterns" in yaml_cfg and yaml_cfg["api_patterns"] is not None:
        api_patterns = list(yaml_cfg["api_patterns"])
    else:
        api_patterns = [
            "**/api/**", "**/graphql**", "**/v1/**", "**/v2/**", "**/v3/**",
            "**/rest/**", "**/services/**", "**/*.json*"
        ]

    if "ignored_extensions" in yaml_cfg and yaml_cfg["ignored_extensions"] is not None:
        ignored_extensions = list(yaml_cfg["ignored_extensions"])
    else:
        ignored_extensions = [
            ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".woff", ".woff2", ".ttf", ".eot", ".ico", ".map", ".html"
        ]

    if "monster_string_len" in yaml_cfg and yaml_cfg["monster_string_len"] is not None:
        monster_string_len = int(yaml_cfg["monster_string_len"])
    else:
        monster_string_len = 5000

    if "auth_fault_types" in yaml_cfg and yaml_cfg["auth_fault_types"] is not None:
        auth_fault_types = list(yaml_cfg["auth_fault_types"])
    else:
        auth_fault_types = [
            "token_invalidation",
            "401_unauthorized",
            "403_forbidden",
            "token_corruption",
        ]

    return ApiChaosConfig(
        enabled=enabled,
        fault_rate=rate,
        fault_types=fault_types,
        status_codes=status_codes,
        delay_range_ms=delay_range_ms,
        api_patterns=api_patterns,
        ignored_extensions=ignored_extensions,
        monster_string_len=monster_string_len,
        auth_chaos_enabled=auth_enabled,
        auth_fault_rate=auth_rate,
        auth_fault_types=auth_fault_types,
    )

