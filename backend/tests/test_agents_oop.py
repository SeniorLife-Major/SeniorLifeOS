"""Unit tests for OOP interfaces, strategy implementations, and factory."""

import unittest

from backend.agents import (
    DocumentClassifier,
    DocumentTypeChoice,
    LlmClient,
    LlmClientFactory,
    LlmDocumentClassifier,
    LlmSchemeResolver,
    MockLlmClient,
    SchemeChoice,
    SchemeResolver,
)
from backend.core.config import GroqConfig, LlmSection


class AgentOopTests(unittest.TestCase):
    def test_mock_client_implements_interface(self):
        client: LlmClient = MockLlmClient(DocumentTypeChoice(document_type="income_certificate", reason="Salary slip"))
        choice = client.generate_structured("dummy prompt", DocumentTypeChoice)
        self.assertEqual(choice.document_type, "income_certificate")
        self.assertEqual(choice.reason, "Salary slip")

    def test_document_classifier_strategy(self):
        mock_client = MockLlmClient(DocumentTypeChoice(document_type="identity", reason="Aadhaar found"))
        classifier: DocumentClassifier = LlmDocumentClassifier(mock_client)

        result = classifier.classify("Name: Meera Rao\nDOB: 1958-05-15")
        self.assertEqual(result.document_type, "identity")
        self.assertEqual(len(mock_client.captured_prompts), 1)
        self.assertIn("Classify this senior's uploaded document", mock_client.captured_prompts[0])

    def test_scheme_resolver_strategy(self):
        mock_client = MockLlmClient(SchemeChoice(scheme_id="demo_senior_support", reason="Pension match"))
        resolver: SchemeResolver = LlmSchemeResolver(mock_client)

        candidates = [{"scheme_id": "demo_senior_support", "name": "Demo Pension"}]
        result = resolver.resolve("I need pension support", candidates)
        self.assertEqual(result.scheme_id, "demo_senior_support")
        self.assertEqual(len(mock_client.captured_prompts), 1)

    def test_factory_creates_groq_provider(self):
        config = LlmSection(provider="groq", groq=GroqConfig(api_key="fake-groq-key", model="openai/gpt-oss-20b"))
        client = LlmClientFactory.create(config)
        self.assertEqual(type(client).__name__, "GroqLlmClient")


if __name__ == "__main__":
    unittest.main()
