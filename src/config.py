"""Configuration loader with environment variable support"""
import os
import re
from pathlib import Path
import yaml

from src.utils import get_logger

logger = get_logger("config")


def load_config(config_path: str = "config.yaml") -> dict:
    """Load config with environment variable substitution

    @param config_path Path to the configuration file
    @return Configuration dictionary with environment variables substituted
    """
    logger.debug(f"加载配置文件 | path={config_path}")
    path = Path(config_path)
    if not path.exists():
        logger.warning(f"配置文件不存在 | path={config_path}")
        return {}

    with open(path, encoding="utf-8") as f:
        content = f.read()

    # First load to preserve ${VAR} syntax in comments
    config = yaml.safe_load(content) or {}

    # Then substitute environment variables in string values
    # Keep ${VAR} syntax if env var is not set
    pattern = r'\$\{([^}]+)\}'

    def replace_env_var(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))  # Keep ${VAR} if not set

    def substitute_vars(obj):
        if isinstance(obj, dict):
            return {k: substitute_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [substitute_vars(item) for item in obj]
        elif isinstance(obj, str):
            return re.sub(pattern, replace_env_var, obj)
        return obj

    config = substitute_vars(config)

    logger.debug(f"配置加载完成 | keys={list(config.keys())}")
    return config


def save_config(config: dict, config_path: str = "config.yaml") -> bool:
    """Save configuration dictionary to YAML file

    Preserves ${VAR} environment variable syntax in the original file.

    @param config Configuration dictionary to save
    @param config_path Path to the configuration file
    @return True if save successful, False otherwise
    """
    logger.debug(f"保存配置文件 | path={config_path} | keys={list(config.keys())}")
    try:
        path = Path(config_path)
        with open(path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)
        logger.info(f"配置保存成功 | path={config_path}")
        return True
    except Exception as e:
        logger.error(f"配置保存失败 | path={config_path} | error={e}", exc_info=True)
        return False


def update_provider_config(config: dict, provider: str, settings: dict) -> dict:
    """Update configuration for a specific provider

    @param config Configuration dictionary
    @param provider Provider name: "anthropic", "openai", "ollama", "lmstudio", "custom"
    @param settings Dictionary of settings to update
    @return Updated configuration dictionary
    """
    logger.debug(f"更新供应商配置 | provider={provider} | settings={list(settings.keys())}")

    if "models" not in config:
        config["models"] = {}

    if provider not in config["models"]:
        config["models"][provider] = {}

    config["models"][provider].update(settings)
    logger.debug(f"供应商配置更新完成 | provider={provider}")
    return config


def set_default_provider(config: dict, provider: str) -> dict:
    """Set the default provider in configuration

    @param config Configuration dictionary
    @param provider Provider name to set as default
    @return Updated configuration dictionary
    """
    logger.debug(f"设置默认供应商 | provider={provider}")

    if "models" not in config:
        config["models"] = {}
    config["models"]["default"] = provider

    logger.debug(f"默认供应商设置完成 | provider={provider}")
    return config


def get_configured_providers(config: dict) -> list[str]:
    """Get list of configured providers

    Excludes the 'default' field from the list.

    @param config Configuration dictionary
    @return List of configured provider names
    """
    models = config.get("models", {})
    providers = [k for k in models.keys() if k != "default"]
    logger.debug(f"获取已配置供应商 | providers={providers}")
    return providers


__all__ = ["load_config", "save_config", "update_provider_config", "set_default_provider", "get_configured_providers"]