"""Tests for chat model selection and .env loading. No network calls; keys are fake."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from backend.agents import models
from backend.utils.env import load_env_file


LLM_KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "SENIORLIFE_LLM_PROVIDER", "SENIORLIFE_LLM_MODEL")


def clean_env(**values: str) -> dict[str, str]:
    """The current environment without any LLM settings, plus the given ones."""
    env = {key: value for key, value in os.environ.items() if key not in LLM_KEYS}
    env.update(values)
    return env


class Answer(BaseModel):
    text: str


class EnvFileTests(unittest.TestCase):
    def write_env(self, content: str) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / ".env"
        path.write_text(content, encoding="utf-8")
        return path

    def test_reads_plain_quoted_and_export_lines(self):
        path = self.write_env(
            "# comment\n\nGROQ_API_KEY=fake-groq\nSENIORLIFE_LLM_MODEL=\"some-model\"\n"
            "export SENIORLIFE_LLM_PROVIDER='groq'\nnot a setting\n"
        )
        with patch.dict(os.environ, clean_env(), clear=True):
            loaded = load_env_file(path)
            self.assertEqual(os.environ["GROQ_API_KEY"], "fake-groq")
            self.assertEqual(os.environ["SENIORLIFE_LLM_MODEL"], "some-model")
            self.assertEqual(os.environ["SENIORLIFE_LLM_PROVIDER"], "groq")
        self.assertEqual(sorted(loaded), ["GROQ_API_KEY", "SENIORLIFE_LLM_MODEL", "SENIORLIFE_LLM_PROVIDER"])

    def test_real_environment_wins_over_file(self):
        path = self.write_env("GROQ_API_KEY=from-file\n")
        with patch.dict(os.environ, clean_env(GROQ_API_KEY="from-shell"), clear=True):
            loaded = load_env_file(path)
            self.assertEqual(os.environ["GROQ_API_KEY"], "from-shell")
        self.assertEqual(loaded, [])

    def test_no_file_loads_nothing(self):
        self.assertEqual(load_env_file(None), [])


@patch("backend.agents.models._load_env")
class ModelSelectionTests(unittest.TestCase):
    def test_default_provider_is_groq(self, _):
        with patch.dict(os.environ, clean_env(GROQ_API_KEY="fake-groq"), clear=True):
            model = models.get_model()
        self.assertEqual(type(model).__name__, "ChatGroq")
        self.assertEqual(model.model_name, models.DEFAULT_GROQ_MODEL)
        # Pinned: confirmed available on Groq by the 2026-10-19 smoke test.
        self.assertEqual(models.DEFAULT_GROQ_MODEL, "openai/gpt-oss-20b")
        self.assertEqual(model.groq_api_key.get_secret_value(), "fake-groq")

    def test_groq_model_name_can_be_overridden(self, _):
        with patch.dict(os.environ, clean_env(GROQ_API_KEY="fake-groq", SENIORLIFE_LLM_MODEL="other-model"), clear=True):
            model = models.get_model()
        self.assertEqual(model.model_name, "other-model")

    def test_missing_groq_key_is_a_clear_error(self, _):
        with patch.dict(os.environ, clean_env(), clear=True):
            with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY"):
                models.get_model()

    def test_openai_provider_needs_key_and_model(self, _):
        with patch.dict(os.environ, clean_env(SENIORLIFE_LLM_PROVIDER="openai"), clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                models.get_model()
        with patch.dict(os.environ, clean_env(SENIORLIFE_LLM_PROVIDER="openai", OPENAI_API_KEY="fake-openai"), clear=True):
            with self.assertRaisesRegex(RuntimeError, "SENIORLIFE_LLM_MODEL"):
                models.get_model()
        with patch.dict(os.environ, clean_env(SENIORLIFE_LLM_PROVIDER="openai", OPENAI_API_KEY="fake-openai", SENIORLIFE_LLM_MODEL="gpt-model"), clear=True):
            model = models.get_model()
        self.assertEqual(type(model).__name__, "ChatOpenAI")
        self.assertEqual(model.model_name, "gpt-model")

    def test_unknown_provider_is_rejected(self, _):
        with patch.dict(os.environ, clean_env(SENIORLIFE_LLM_PROVIDER="someone-else"), clear=True):
            with self.assertRaisesRegex(RuntimeError, "SENIORLIFE_LLM_PROVIDER"):
                models.get_model()

    def test_groq_uses_function_calling_for_structured_output(self, _):
        fake = MagicMock()
        with patch.dict(os.environ, clean_env(), clear=True), patch("backend.agents.models.get_model", return_value=fake):
            models.structured_model(Answer)
        fake.with_structured_output.assert_called_once_with(Answer, method="function_calling")

    def test_openai_keeps_default_structured_output(self, _):
        fake = MagicMock()
        with patch.dict(os.environ, clean_env(SENIORLIFE_LLM_PROVIDER="openai"), clear=True), \
                patch("backend.agents.models.get_model", return_value=fake):
            models.structured_model(Answer)
        fake.with_structured_output.assert_called_once_with(Answer)


if __name__ == "__main__":
    unittest.main()
