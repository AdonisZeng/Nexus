"""Config: loading, env substitution, sensitive field removal, provider helpers."""
import pytest

from src.config import (
    load_config,
    save_config,
    update_provider_config,
    set_default_provider,
    get_configured_providers,
)


class TestLoadConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_config(str(tmp_path / "nope.yaml")) == {}

    def test_env_var_substitution(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_NEXUS_KEY", "secret-123")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "models:\n  anthropic:\n    api_key: ${TEST_NEXUS_KEY}\n",
            encoding="utf-8",
        )
        config = load_config(str(cfg_file))
        assert config["models"]["anthropic"]["api_key"] == "secret-123"

    def test_unset_env_var_kept_as_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_NEXUS_UNSET", raising=False)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("key: ${TEST_NEXUS_UNSET}\n", encoding="utf-8")
        config = load_config(str(cfg_file))
        assert config["key"] == "${TEST_NEXUS_UNSET}"

    def test_nested_list_substitution(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_NEXUS_ITEM", "v")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("items:\n  - ${TEST_NEXUS_ITEM}\n  - plain\n", encoding="utf-8")
        config = load_config(str(cfg_file))
        assert config["items"] == ["v", "plain"]


class TestSaveConfig:
    def test_sensitive_fields_removed(self, tmp_path):
        cfg_file = tmp_path / "out.yaml"
        config = {
            "models": {
                "anthropic": {"api_key": "REAL-SECRET", "model": "claude"},
            }
        }
        assert save_config(config, str(cfg_file)) is True
        reloaded = load_config(str(cfg_file))
        assert "api_key" not in reloaded["models"]["anthropic"]
        assert reloaded["models"]["anthropic"]["model"] == "claude"
        # original dict untouched
        assert config["models"]["anthropic"]["api_key"] == "REAL-SECRET"

    def test_nested_list_sensitive_removed(self, tmp_path):
        cfg_file = tmp_path / "out.yaml"
        config = {"servers": [{"name": "s", "api_key": "x"}]}
        save_config(config, str(cfg_file))
        reloaded = load_config(str(cfg_file))
        assert "api_key" not in reloaded["servers"][0]


class TestProviderHelpers:
    def test_update_provider_config_creates_sections(self):
        config = {}
        update_provider_config(config, "ollama", {"model": "llama3"})
        assert config["models"]["ollama"] == {"model": "llama3"}

    def test_set_default_provider(self):
        config = {}
        set_default_provider(config, "openai")
        assert config["models"]["default"] == "openai"

    def test_get_configured_providers_excludes_default(self):
        config = {"models": {"default": "openai", "openai": {}, "ollama": {}}}
        providers = get_configured_providers(config)
        assert set(providers) == {"openai", "ollama"}
