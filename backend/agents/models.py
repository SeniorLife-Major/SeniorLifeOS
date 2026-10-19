"""Chat model setup. Groq during the docs phase; OpenAI afterwards.

Pick the provider with SENIORLIFE_LLM_PROVIDER (groq or openai, default groq).
Keys come only from environment variables, which may be loaded from .env.
Groq uses langchain-groq; OpenAI uses langchain-openai.
"""

from __future__ import annotations

import os

from ..utils.env import find_env_file, load_env_file


DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
PROVIDERS = {"groq", "openai"}


def _load_env() -> None:
    load_env_file(find_env_file())


def current_provider() -> str:
    provider = os.getenv("SENIORLIFE_LLM_PROVIDER", "groq").strip().lower()
    if provider not in PROVIDERS:
        raise RuntimeError(f"SENIORLIFE_LLM_PROVIDER must be one of: {', '.join(sorted(PROVIDERS))}")
    return provider


def groq_model():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Set GROQ_API_KEY before using use_llm=True")
    from langchain_groq import ChatGroq

    model_name = os.getenv("SENIORLIFE_LLM_MODEL") or DEFAULT_GROQ_MODEL
    return ChatGroq(model=model_name, api_key=api_key, temperature=0)


def openai_model():
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY before using use_llm=True")
    model_name = os.getenv("SENIORLIFE_LLM_MODEL")
    if not model_name:
        raise RuntimeError("Set SENIORLIFE_LLM_MODEL before using use_llm=True")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model_name, temperature=0)


def get_model():
    _load_env()
    return groq_model() if current_provider() == "groq" else openai_model()


def structured_model(schema):
    """Return a model that answers with an instance of the given Pydantic schema."""
    model = get_model()
    if current_provider() == "groq":
        # Tool calling works on more Groq models than json_schema. Explicit so a library default change can't alter it.
        return model.with_structured_output(schema, method="function_calling")
    return model.with_structured_output(schema)
