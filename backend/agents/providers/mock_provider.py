"""Mock implementation of LlmClient interface for tests and local demo mode."""

from __future__ import annotations

from typing import Any, TypeVar
from pydantic import BaseModel

from ..interfaces import LlmClient

T = TypeVar("T", bound=BaseModel)


class MockLlmClient(LlmClient):
    """Deterministic mock client returning pre-configured responses for tests."""

    def __init__(self, predefined_response: Any | None = None) -> None:
        self.predefined_response = predefined_response
        self.captured_prompts: list[str] = []

    def set_response(self, response: Any) -> None:
        self.predefined_response = response

    def generate_structured(self, prompt: str, schema: type[T]) -> T:
        self.captured_prompts.append(prompt)
        if self.predefined_response is not None:
            if isinstance(self.predefined_response, schema):
                return self.predefined_response
            if isinstance(self.predefined_response, dict):
                return schema.model_validate(self.predefined_response)
        raise RuntimeError("MockLlmClient called without configuring a predefined_response")
