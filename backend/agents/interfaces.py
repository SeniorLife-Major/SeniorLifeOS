"""Abstract Base Class (Interface) definitions for LLM providers and AI agents.

Enforces OOP contracts across the codebase, mirroring Java interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, TypeVar
from pydantic import BaseModel, Field

from ..common.schemas import CamelModel

T = TypeVar("T", bound=BaseModel)


# Structured Decision DTOs
class DocumentTypeChoice(CamelModel):
    document_type: Literal["identity", "income_certificate", "residence_proof", "unknown"]
    reason: str = Field(description="Short evidence from the document text")


class SchemeChoice(CamelModel):
    scheme_id: str = Field(description="One allowed scheme ID, or empty if unclear")
    reason: str = Field(description="Short explanation of the choice")


# Interfaces
class LlmClient(ABC):
    """Interface for LLM clients (Groq, OpenAI, Mock, etc.)."""

    @abstractmethod
    def generate_structured(self, prompt: str, schema: type[T]) -> T:
        """Invoke LLM and return a validated instance of schema T."""
        pass


class DocumentClassifier(ABC):
    """Interface for document type classification."""

    @abstractmethod
    def classify(self, page_text: str, known_values: dict[str, str] | None = None) -> DocumentTypeChoice:
        """Classify document type from text content with privacy redaction."""
        pass


class SchemeResolver(ABC):
    """Interface for government scheme resolution."""

    @abstractmethod
    def resolve(self, need_text: str, candidates: list[dict[str, Any]]) -> SchemeChoice:
        """Match senior need text against curated scheme candidates."""
        pass
