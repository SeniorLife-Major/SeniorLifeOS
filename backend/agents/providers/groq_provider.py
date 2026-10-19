"""Groq implementation of LlmClient interface."""

from __future__ import annotations

from typing import TypeVar
from pydantic import BaseModel
from langchain_groq import ChatGroq

from ..interfaces import LlmClient

T = TypeVar("T", bound=BaseModel)


class GroqLlmClient(LlmClient):
    """Concrete Groq LLM client using ChatGroq function calling."""

    def __init__(self, api_key: str, model_name: str = "openai/gpt-oss-20b", temperature: float = 0.0) -> None:
        if not api_key:
            raise RuntimeError("Set GROQ_API_KEY before using use_llm=True")
        self.model_name = model_name
        self._chat = ChatGroq(model=model_name, api_key=api_key, temperature=temperature)

    def generate_structured(self, prompt: str, schema: type[T]) -> T:
        # Tool/function calling works reliably across Groq models
        structured = self._chat.with_structured_output(schema, method="function_calling")
        return structured.invoke(prompt)
