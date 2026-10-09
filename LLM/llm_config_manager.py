#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-user configuration for the fixed DeepSeek API integration.

The local workbench has one provider and one model. Users only supply their
own API key; the endpoint, model, context window, output limit, and reasoning
mode remain locked by the application.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from infrastructure.paths import runtime_config_path
from infrastructure.secure_files import write_private_text


log = logging.getLogger(__name__)

DEEPSEEK_PROVIDER = "deepseek"
LLM_CONFIG_FILE = runtime_config_path("llm_config.json")
CONFIG_DIR = LLM_CONFIG_FILE.parent


def _read_deepseek_api_key(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    record = payload.get(DEEPSEEK_PROVIDER) if isinstance(payload, dict) else None
    if not isinstance(record, dict):
        return ""
    return str(record.get("api_key") or "").strip()


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    name: str
    enabled: bool = True
    is_custom: bool = False
    context_window: int = 1_000_000
    max_output_tokens: int = 393_216
    enable_thinking: bool = True
    thinking_budget: int = 0
    reasoning_effort: Optional[str] = "max"


def normalize_deepseek_reasoning_effort(value: str | None, default: str = "max") -> str:
    """Use the provider vocabulary, including migration of the old medium value."""
    effort = str(value or "").strip().lower()
    if effort == "medium":
        effort = "high"
    return effort if effort in {"low", "high", "max"} else default


def deepseek_reasoning_options(reasoning_effort: str = "max") -> dict[str, Any]:
    """Return the reasoning options shared by test and production calls."""
    return {
        "reasoning_effort": normalize_deepseek_reasoning_effort(reasoning_effort),
        "extra_body": {"thinking": {"type": "enabled"}},
    }


class LLMConfigManager:
    """Persist one local user's key for the application's fixed DeepSeek model."""

    DEFAULT_CONFIGS = {
        DEEPSEEK_PROVIDER: {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "is_custom": False,
            "context_window": 1_000_000,
            "max_output_tokens": 393_216,
            "enable_thinking": True,
            "reasoning_effort": "max",
            "display_name": "DeepSeek V4.1 Flash",
            "context_label": "1M",
            "max_output_label": "384K",
        }
    }

    def __init__(self):
        self.configs: dict[str, LLMConfig] = {}
        self._config_path = LLM_CONFIG_FILE
        self.load_configs()

    def _locked_deepseek_config(self, api_key: str) -> dict[str, Any]:
        defaults = self.DEFAULT_CONFIGS[DEEPSEEK_PROVIDER]
        return {
            "provider": DEEPSEEK_PROVIDER,
            "api_key": str(api_key or "").strip(),
            "base_url": defaults["base_url"],
            "model": defaults["model"],
            "name": defaults["display_name"],
            "enabled": True,
            "is_custom": False,
            "context_window": defaults["context_window"],
            "max_output_tokens": defaults["max_output_tokens"],
            "enable_thinking": defaults["enable_thinking"],
            "thinking_budget": 0,
            "reasoning_effort": defaults["reasoning_effort"],
        }

    def load_configs(self) -> None:
        """Load only this runtime's local key; never inherit old desktop keys."""
        self.configs = {}
        self._config_path = LLM_CONFIG_FILE
        api_key = _read_deepseek_api_key(self._config_path)
        if api_key:
            self.configs[DEEPSEEK_PROVIDER] = LLMConfig(**self._locked_deepseek_config(api_key))

    def save_configs(self) -> bool:
        """Store this user's configuration in a private file outside the source."""
        path = self._config_path
        try:
            payload = {
                provider: asdict(item)
                for provider, item in self.configs.items()
                if provider == DEEPSEEK_PROVIDER and item.api_key
            }
            write_private_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
            return True
        except OSError as exc:
            log.error("保存 DeepSeek 配置失败: %s", exc)
            return False

    def set_config(self, provider: str, api_key: str) -> bool:
        """Save a key without exposing any model or endpoint overrides."""
        if provider != DEEPSEEK_PROVIDER or not str(api_key or "").strip():
            return False
        previous = self.configs.get(DEEPSEEK_PROVIDER)
        self.configs[DEEPSEEK_PROVIDER] = LLMConfig(**self._locked_deepseek_config(api_key))
        if self.save_configs():
            return True
        if previous is None:
            self.configs.pop(DEEPSEEK_PROVIDER, None)
        else:
            self.configs[DEEPSEEK_PROVIDER] = previous
        return False

    def clear_builtin_config(self, provider: str) -> tuple[bool, str]:
        if provider != DEEPSEEK_PROVIDER:
            return False, f"不支持的内置提供商: {provider}"
        previous = self.configs.pop(DEEPSEEK_PROVIDER, None)
        if self.save_configs():
            return True, "DeepSeek API Key 已清除"
        if previous is not None:
            self.configs[DEEPSEEK_PROVIDER] = previous
        return False, "清除失败"

    def get_config(self, provider: str | None) -> Optional[LLMConfig]:
        return self.configs.get(str(provider or ""))

    def get_default_provider(self) -> str:
        return DEEPSEEK_PROVIDER

    def get_enabled_providers(self) -> list[str]:
        config = self.configs.get(DEEPSEEK_PROVIDER)
        return [DEEPSEEK_PROVIDER] if config and config.enabled and config.api_key else []

    def list_configs(self) -> dict[str, dict[str, Any]]:
        config = self.configs.get(DEEPSEEK_PROVIDER)
        public = self._locked_deepseek_config(config.api_key if config else "")
        public.pop("api_key", None)
        public["enabled"] = bool(config and config.api_key)
        public["has_api_key"] = bool(config and config.api_key)
        defaults = self.DEFAULT_CONFIGS[DEEPSEEK_PROVIDER]
        public["context_label"] = defaults["context_label"]
        public["max_output_label"] = defaults["max_output_label"]
        return {DEEPSEEK_PROVIDER: public}

    def test_config(
        self,
        provider: str = DEEPSEEK_PROVIDER,
        api_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Test the fixed DeepSeek model with a temporary or saved key."""
        if provider != DEEPSEEK_PROVIDER:
            return {"success": False, "message": f"不支持的提供商: {provider}", "provider": provider}
        config = self.configs.get(DEEPSEEK_PROVIDER)
        effective_key = str(api_key or (config.api_key if config else "")).strip()
        if not effective_key:
            return {
                "success": False,
                "message": "DeepSeek API Key 不能为空",
                "provider": DEEPSEEK_PROVIDER,
            }
        defaults = self.DEFAULT_CONFIGS[DEEPSEEK_PROVIDER]
        try:
            from infrastructure.llm_transport import default_llm_timeout
            from openai import OpenAI

            client = OpenAI(api_key=effective_key, base_url=defaults["base_url"])
            client.chat.completions.create(
                model=defaults["model"],
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=10,
                timeout=default_llm_timeout(read_seconds=120.0),
                **deepseek_reasoning_options(),
            )
            return {
                "success": True,
                "message": "DeepSeek 连接有效",
                "provider": DEEPSEEK_PROVIDER,
                "model": defaults["model"],
            }
        except Exception as exc:
            detail = str(exc).replace(effective_key, "[redacted]")[:1000]
            return {
                "success": False,
                "message": f"DeepSeek 连接测试失败: {detail}",
                "provider": DEEPSEEK_PROVIDER,
                "model": defaults["model"],
            }


_config_manager: LLMConfigManager | None = None


def get_config_manager() -> LLMConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = LLMConfigManager()
    return _config_manager


def get_llm_client(provider: Optional[str] = None):
    manager = get_config_manager()
    selected = provider or manager.get_default_provider()
    if selected != DEEPSEEK_PROVIDER:
        raise ValueError(f"不支持的 LLM 提供商: {selected}")
    config = manager.get_config(selected)
    if config is None or not config.api_key:
        raise ValueError("未配置 DeepSeek API Key")
    from openai import OpenAI

    return OpenAI(api_key=config.api_key, base_url=config.base_url)


def get_llm_client_with_fallback(preferred_provider: Optional[str] = None):
    manager = get_config_manager()
    selected = preferred_provider or manager.get_default_provider()
    config = manager.get_config(selected)
    if selected != DEEPSEEK_PROVIDER or config is None or not config.api_key:
        raise ValueError("DeepSeek API 配置不可用")
    return get_llm_client(selected), selected, config
