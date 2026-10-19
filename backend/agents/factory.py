"""Factory for creating LlmClient instances based on application configuration."""

from __future__ import annotations

import os
from ..core.config import LlmSection
from .interfaces import LlmClient
from .providers.groq_provider import GroqLlmClient
from .providers.openai_provider import OpenAiLlmClient


class LlmClientFactory:
    """Factory to instantiate the appropriate LlmClient based on configuration."""

    @staticmethod
    def create(config: LlmSection) -> LlmClient:
        provider = (config.provider or os.getenv("SENIORLIFE_LLM_PROVIDER", "groq")).strip().lower()

        if provider == "groq":
            groq_cfg = config.groq
            api_key = groq_cfg.api_key or os.getenv("GROQ_API_KEY", "")
            model_name = groq_cfg.model or os.getenv("SENIORLIFE_LLM_MODEL", "openai/gpt-oss-20b")
            return GroqLlmClient(api_key=api_key, model_name=model_name, temperature=groq_cfg.temperature)

        if provider == "openai":
            openai_cfg = config.openai
            api_key = openai_cfg.api_key or os.getenv("OPENAI_API_KEY", "")
            model_name = openai_cfg.model or os.getenv("SENIORLIFE_LLM_MODEL", "")
            return OpenAiLlmClient(api_key=api_key, model_name=model_name, temperature=openai_cfg.temperature)

        raise RuntimeError(f"Unsupported LLM provider '{provider}'. Must be one of: groq, openai")
