"""
Configuration management for the application.

Handles loading, validating, and persisting application configuration.
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, validator

from .models import AppConfig, SimTradingConfig, AISourceConfig, AIProviderConfig

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Manages application configuration.

    Handles loading from disk, validation, and persistence.
    """

    DEFAULT_CONFIG_PATH = Path.home() / ".tdx-trend" / "config.json"

    def __init__(self, config_path: Path | None = None):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to config file (defaults to ~/.tdx-trend/config.json)
        """
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config: AppConfig | None = None

    def get_config(self) -> AppConfig:
        """
        Get current configuration, loading from disk if needed.

        Returns:
            Current AppConfig
        """
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def set_config(self, config: AppConfig) -> AppConfig:
        """
        Update configuration and persist to disk.

        Args:
            config: New configuration

        Returns:
            Updated configuration
        """
        self._config = config
        self._save_config(config)
        return config

    def update_sim_trading_config(self, sim_config: SimTradingConfig) -> SimTradingConfig:
        """
        Update simulation trading configuration.

        Args:
            sim_config: New simulation config

        Returns:
            Updated simulation config
        """
        config = self.get_config()
        config.sim_trading = sim_config
        return self.set_config(config).sim_trading

    def update_ai_source_config(self, source_config: AISourceConfig) -> AISourceConfig:
        """
        Update AI source configuration.

        Args:
            source_config: New AI source config

        Returns:
            Updated AI source config
        """
        config = self.get_config()
        config.ai_source = source_config
        return self.set_config(config).ai_source

    def get_active_ai_provider(self) -> AIProviderConfig | None:
        """
        Get the currently active AI provider.

        Returns:
            Active AIProviderConfig or None
        """
        config = self.get_config()
        for provider in config.ai_providers:
            if provider.enabled:
                return provider
        return None

    def _load_config(self) -> AppConfig:
        """Load configuration from disk or return defaults."""
        if not self.config_path.exists():
            logger.info(f"Config file not found at {self.config_path}, using defaults")
            return self._default_config()

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppConfig(**data)
        except Exception as e:
            logger.error(f"Failed to load config from {self.config_path}: {e}")
            return self._default_config()

    def _save_config(self, config: AppConfig) -> None:
        """
        Save configuration to disk.

        Args:
            config: Configuration to save
        """
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config.dict(), f, indent=2, ensure_ascii=False)
            logger.info(f"Saved config to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save config to {self.config_path}: {e}")
            # Don't raise - keep runtime available even if persistence fails

    def _default_config(self) -> AppConfig:
        """Create default configuration."""
        return AppConfig(
            tdx_data_path="",
            market_data_source="tdx_then_akshare",
            akshare_cache_dir="",
            api_keys_path="",
            sim_trading=self._default_sim_config(),
            ai_source=self._default_ai_source_config(),
            ai_providers=[],
        )

    def _default_sim_config(self) -> SimTradingConfig:
        """Create default simulation trading configuration."""
        return SimTradingConfig(
            initial_capital=1_000_000.0,
            commission_rate=0.0003,
            min_commission=5.0,
            stamp_tax_rate=0.001,
            slippage_rate=0.001,
            t_plus_one=True,
        )

    def _default_ai_source_config(self) -> AISourceConfig:
        """Create default AI source configuration."""
        return AISourceConfig(
            enabled=False,
            source_urls=[],
            industry_source_urls=[],
        )


class ConfigValidator:
    """
    Validates configuration values.

    Ensures configuration is within acceptable ranges and formats.
    """

    @staticmethod
    def validate_sim_trading_config(config: SimTradingConfig) -> list[str]:
        """
        Validate simulation trading configuration.

        Args:
            config: Config to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if config.initial_capital <= 0:
            errors.append("initial_capital must be positive")

        if config.commission_rate < 0:
            errors.append("commission_rate cannot be negative")

        if config.min_commission < 0:
            errors.append("min_commission cannot be negative")

        if config.stamp_tax_rate < 0:
            errors.append("stamp_tax_rate cannot be negative")

        if config.slippage_rate < 0:
            errors.append("slippage_rate cannot be negative")

        return errors

    @staticmethod
    def validate_ai_provider_config(config: AIProviderConfig) -> list[str]:
        """
        Validate AI provider configuration.

        Args:
            config: Config to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if not config.provider.strip():
            errors.append("provider name cannot be empty")

        if not config.base_url.strip():
            errors.append("base_url cannot be empty")

        if not config.model.strip():
            errors.append("model cannot be empty")

        # Validate URL format
        if config.base_url.strip():
            try:
                from urllib.parse import urlparse
                result = urlparse(config.base_url)
                if not all([result.scheme, result.netloc]):
                    errors.append("base_url must be a valid URL")
            except Exception:
                errors.append("base_url is not a valid URL")

        return errors

    @staticmethod
    def validate_app_config(config: AppConfig) -> list[str]:
        """
        Validate complete application configuration.

        Args:
            config: Config to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Validate sim trading config
        sim_errors = ConfigValidator.validate_sim_trading_config(config.sim_trading)
        errors.extend(sim_errors)

        # Validate each AI provider
        for idx, provider in enumerate(config.ai_providers):
            provider_errors = ConfigValidator.validate_ai_provider_config(provider)
            for error in provider_errors:
                errors.append(f"ai_providers[{idx}]: {error}")

        return errors


def create_config_manager(config_path: str | None = None) -> ConfigManager:
    """
    Factory function to create ConfigManager.

    Args:
        config_path: Optional path to config file

    Returns:
        ConfigManager instance
    """
    path = Path(config_path) if config_path else None
    return ConfigManager(config_path=path)
