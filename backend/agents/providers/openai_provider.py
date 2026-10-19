"""OpenAI implementation of LlmClient interface."""

from __future__ import annotations

from typing import TypeVar
from pydantic import BaseModel
from langchain_openai import ChatOpenAI

from ..interfaces import LlmClient

T = TypeVar("T", bound=BaseModel)


class OpenAiLlmClient(LlmClient):
    """Concrete OpenAI LLM client using ChatOpenAI structured output."""

    def __init__(self, api_key: str, model_name: str, temperature: float = 0.0) -> None:
        if not api_key:
            raise RuntimeError("Set OPENAI_API_KEY before using use_llm=True")
        if not model_name:
            raise RuntimeError("Set SENIORLIFE_LLM_MODEL before using use_llm=True")
        self.model_name = model_name
        self._chat = ChatOpenAI(model=model_name, api_key=api_key, temperature=temperature)

    def generate_structured(self, prompt: str, schema: type[T]) -> T:
        structured = self._chat.with_structured_output(schema)
        return structured.invoke(prompt)
