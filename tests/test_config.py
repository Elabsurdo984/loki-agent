import json
from pathlib import Path
import yaml

from src.loki.config import (
    DEFAULT_MODEL,
    activate_model_profile,
    add_model_profile,
    clear_active_model_profile,
    get_active_model_profile,
    get_model_profile,
    is_ai_customized,
    list_model_profiles,
    load_models_registry,
    model_source,
    remove_model_profile,
    resolve_ai_connection,
    resolve_model,
)


def test_add_model_profile_creates_and_updates_case_insensitively(tmp_path: Path, monkeypatch):
    """Verify add_model_profile creates a profile and updates on case-insensitive duplicate."""
    monkeypatch.chdir(tmp_path)

    # 1. Add new profile
    p1 = add_model_profile("Local", "ollama/llama3", api_base="http://localhost:11434")
    assert p1["name"] == "Local"
    assert p1["model"] == "ollama/llama3"
    assert p1["api_base"] == "http://localhost:11434"
    assert len(list_model_profiles()) == 1

    # 2. Add with same name different casing updates rather than duplicates
    p2 = add_model_profile(
        "local",
        "ollama/llama3:8b",
        api_base="http://localhost:11434/v1",
        api_key_env="OLLAMA_API_KEY",
    )
    profiles = list_model_profiles()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "local"
    assert profiles[0]["model"] == "ollama/llama3:8b"
    assert profiles[0]["api_base"] == "http://localhost:11434/v1"
    assert profiles[0]["api_key_env"] == "OLLAMA_API_KEY"

    # 3. Case-insensitive lookup
    found = get_model_profile("LOCAL")
    assert found is not None
    assert found["name"] == "local"


def test_activate_model_profile_and_resolve_model(tmp_path: Path, monkeypatch):
    """Verify activate_model_profile updates active profile and model resolution."""
    monkeypatch.chdir(tmp_path)

    add_model_profile("Groq", "groq/llama3-70b-8192")
    assert get_active_model_profile() is None

    # Activate using lowercase
    activated = activate_model_profile("groq")
    assert activated is not None
    assert activated["name"] == "Groq"

    active = get_active_model_profile()
    assert active is not None
    assert active["name"] == "Groq"

    # resolve_model returns active model
    assert resolve_model() == "groq/llama3-70b-8192"
    assert model_source() == "/model profile 'Groq'"

    # Activating nonexistent profile returns None and keeps current active
    assert activate_model_profile("nonexistent") is None
    assert get_active_model_profile()["name"] == "Groq"


def test_remove_model_profile_and_clears_active(tmp_path: Path, monkeypatch):
    """Verify remove_model_profile returns boolean and clears active if active removed."""
    monkeypatch.chdir(tmp_path)

    add_model_profile("OpenAI", "openai/gpt-4o")
    add_model_profile("Anthropic", "anthropic/claude-3-5-sonnet")
    activate_model_profile("OpenAI")

    # Removing non-existent profile returns False
    assert remove_model_profile("Mistral") is False
    assert len(list_model_profiles()) == 2
    assert get_active_model_profile()["name"] == "OpenAI"

    # Removing inactive profile returns True and leaves active intact
    assert remove_model_profile("anthropic") is True
    assert len(list_model_profiles()) == 1
    assert get_active_model_profile()["name"] == "OpenAI"

    # Removing active profile returns True and clears active
    assert remove_model_profile("openai") is True
    assert len(list_model_profiles()) == 0
    assert get_active_model_profile() is None
    assert load_models_registry()["active"] is None

    # Removing again returns False
    assert remove_model_profile("openai") is False


def test_clear_active_model_profile(tmp_path: Path, monkeypatch):
    """Verify clear_active_model_profile unsets active profile without removing it."""
    monkeypatch.chdir(tmp_path)

    add_model_profile("vLLM", "hosted_vllm/model")
    activate_model_profile("vLLM")
    assert get_active_model_profile() is not None

    clear_active_model_profile()
    assert get_active_model_profile() is None
    assert len(list_model_profiles()) == 1


def test_resolve_model_explicit_override(tmp_path: Path, monkeypatch):
    """Verify explicit model always wins regardless of active profile or yaml config."""
    monkeypatch.chdir(tmp_path)

    add_model_profile("Ollama", "ollama/mistral")
    activate_model_profile("Ollama")

    config_dir = tmp_path / ".loki"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_yaml = config_dir / "config.yaml"
    config_yaml.write_text(yaml.dump({"ai": {"model": "yaml/model"}}), encoding="utf-8")

    assert resolve_model(explicit="custom/override-model") == "custom/override-model"
    assert model_source(explicit="custom/override-model") == "--model flag"


def test_resolve_model_fallbacks(tmp_path: Path, monkeypatch):
    """Verify fallback chain: default -> config.yaml -> provider prefix."""
    monkeypatch.chdir(tmp_path)

    # 1. Clean workspace falls back to DEFAULT_MODEL
    assert resolve_model() == DEFAULT_MODEL
    assert model_source() == "bundled default"

    config_dir = tmp_path / ".loki"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_yaml = config_dir / "config.yaml"

    # 2. Config yaml with provider and bare model name
    config_yaml.write_text(
        yaml.dump({"ai": {"model": "mistral-large", "provider": "mistral"}}),
        encoding="utf-8",
    )
    assert resolve_model() == "mistral/mistral-large"
    assert model_source() == ".loki/config.yaml"

    # 3. Config yaml with fully qualified model name
    config_yaml.write_text(
        yaml.dump({"ai": {"model": "openai/gpt-4-turbo"}}),
        encoding="utf-8",
    )
    assert resolve_model() == "openai/gpt-4-turbo"


def test_resolve_ai_connection_explicit_model_no_leakage(tmp_path: Path, monkeypatch):
    """Verify explicit_model returns clean connection without inheriting yaml/profile configs."""
    monkeypatch.chdir(tmp_path)

    # Configure yaml with custom api_base and api_key_env
    config_dir = tmp_path / ".loki"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_yaml = config_dir / "config.yaml"
    config_yaml.write_text(
        yaml.dump({
            "ai": {
                "model": "internal/model",
                "api_base": "https://internal.gateway/v1",
                "api_key_env": "INTERNAL_KEY",
            }
        }),
        encoding="utf-8",
    )

    # Configure active profile with its own api_base
    add_model_profile("LocalGateway", "local/model", api_base="http://localhost:8000")
    activate_model_profile("LocalGateway")

    # Resolve with explicit model
    conn = resolve_ai_connection(explicit_model="gpt-4o")
    assert conn == {"model": "gpt-4o"}
    assert "api_base" not in conn
    assert "api_key" not in conn


def test_resolve_ai_connection_active_profile_and_yaml(tmp_path: Path, monkeypatch):
    """Verify resolve_ai_connection populates api_base and env api_key properly."""
    monkeypatch.chdir(tmp_path)

    # 1. From active profile
    monkeypatch.setenv("TEST_CUSTOM_KEY", "secret-token-123")
    add_model_profile(
        "SecureProfile",
        "custom/llm",
        api_base="https://custom.ai/v1",
        api_key_env="TEST_CUSTOM_KEY",
    )
    activate_model_profile("SecureProfile")

    conn = resolve_ai_connection()
    assert conn["model"] == "custom/llm"
    assert conn["api_base"] == "https://custom.ai/v1"
    assert conn["api_key"] == "secret-token-123"

    # 2. Deactivate profile, fallback to config.yaml
    clear_active_model_profile()
    monkeypatch.setenv("YAML_CUSTOM_KEY", "yaml-token-456")
    config_dir = tmp_path / ".loki"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        yaml.dump({
            "ai": {
                "model": "azure/gpt-4",
                "api_base": "https://azure.endpoint/v1",
                "api_key_env": "YAML_CUSTOM_KEY",
            }
        }),
        encoding="utf-8",
    )

    conn_yaml = resolve_ai_connection()
    assert conn_yaml["model"] == "azure/gpt-4"
    assert conn_yaml["api_base"] == "https://azure.endpoint/v1"
    assert conn_yaml["api_key"] == "yaml-token-456"


def test_is_ai_customized_state(tmp_path: Path, monkeypatch):
    """Verify is_ai_customized correctly reflects clean vs customized states."""
    monkeypatch.chdir(tmp_path)

    # 1. Clean workspace
    assert is_ai_customized() is False

    # 2. Explicit model flag
    assert is_ai_customized(explicit_model="gpt-4o") is True
    assert is_ai_customized() is False

    # 3. Active profile makes it customized
    add_model_profile("Fast", "groq/llama3")
    activate_model_profile("Fast")
    assert is_ai_customized() is True

    # 4. Cleared profile reverts to clean
    clear_active_model_profile()
    assert is_ai_customized() is False

    # 5. Config yaml settings customize it
    config_dir = tmp_path / ".loki"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_yaml = config_dir / "config.yaml"

    config_yaml.write_text(yaml.dump({"ai": {"model": "custom/model"}}), encoding="utf-8")
    assert is_ai_customized() is True

    config_yaml.write_text(yaml.dump({"ai": {"api_base": "http://localhost:8000"}}), encoding="utf-8")
    assert is_ai_customized() is True

    config_yaml.write_text(yaml.dump({"ai": {"api_key_env": "MY_KEY"}}), encoding="utf-8")
    assert is_ai_customized() is True


def test_load_models_registry_corrupted_or_invalid(tmp_path: Path, monkeypatch):
    """Verify load_models_registry gracefully handles nonexistent, corrupted, or non-dict files."""
    monkeypatch.chdir(tmp_path)

    # Missing file
    assert load_models_registry() == {"active": None, "profiles": []}

    models_path = tmp_path / ".loki" / "models.json"
    models_path.parent.mkdir(parents=True, exist_ok=True)

    # Corrupted JSON
    models_path.write_text("{not valid json", encoding="utf-8")
    assert load_models_registry() == {"active": None, "profiles": []}

    # Non-dict JSON
    models_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert load_models_registry() == {"active": None, "profiles": []}
