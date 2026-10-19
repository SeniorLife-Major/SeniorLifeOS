"""Agents package exposing OOP interfaces, providers, and factory."""

from .classifiers import LlmDocumentClassifier, LlmSchemeResolver
from .factory import LlmClientFactory
from .interfaces import (
    DocumentClassifier,
    DocumentTypeChoice,
    LlmClient,
    SchemeChoice,
    SchemeResolver,
)
from .llm_agents import choose_document_type, choose_scheme
from .providers import GroqLlmClient, MockLlmClient, OpenAiLlmClient

__all__ = [
    # Interfaces
    "LlmClient",
    "DocumentClassifier",
    "SchemeResolver",
    "DocumentTypeChoice",
    "SchemeChoice",
    # Implementations / Providers
    "GroqLlmClient",
    "OpenAiLlmClient",
    "MockLlmClient",
    "LlmDocumentClassifier",
    "LlmSchemeResolver",
    # Factory
    "LlmClientFactory",
    # Legacy facades
    "choose_document_type",
    "choose_scheme",
]
