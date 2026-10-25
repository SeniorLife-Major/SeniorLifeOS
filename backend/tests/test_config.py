"""Unit tests for YAML config loading with environment variable and fallback resolution."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core.config import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        # load_config() loads the project's .env into os.environ. Tests must never see the
        # real .env: its keys would replace the fallbacks and could show up in failure messages.
        patcher = patch("backend.utils.env.find_env_file", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_env_file_is_loaded_from_the_path_find_env_file_gives(self):
        # Shows the patch above hits the right place: a stand-in .env given through it is used.
        config_yaml = "llm:\n  groq:\n    api_key: ${GROQ_API_KEY:fallback}\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "test_application.yml"
            path.write_text(config_yaml, encoding="utf-8")
            env_file = Path(tmp_dir) / ".env"
            env_file.write_text("GROQ_API_KEY=from-stand-in-env-file\n", encoding="utf-8")
            with patch("backend.utils.env.find_env_file", return_value=env_file), \
                    patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_config(path).llm.groq.api_key, "from-stand-in-env-file")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_config(path).llm.groq.api_key, "fallback")

    def test_loads_yaml_with_fallback_defaults(self):
        config_yaml = """
app:
  name: ${APP_NAME:Fallback App}
  version: ${APP_VERSION:2.0.0}
  debug: ${DEBUG:true}

server:
  host: ${SERVER_HOST:127.0.0.1}
  port: ${SERVER_PORT:9000}

database:
  url: ${DATABASE_URL:postgresql+asyncpg://user:pass@localhost:5432/testdb}
  echo: ${DATABASE_ECHO:false}
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "test_application.yml"
            path.write_text(config_yaml, encoding="utf-8")

            # With no env vars set, fallbacks are used
            with patch.dict(os.environ, {}, clear=True):
                cfg = load_config(path)
                self.assertEqual(cfg.app.name, "Fallback App")
                self.assertEqual(cfg.app.version, "2.0.0")
                self.assertTrue(cfg.app.debug)
                self.assertEqual(cfg.server.host, "127.0.0.1")
                self.assertEqual(cfg.server.port, 9000)
                self.assertFalse(cfg.database.echo)

    def test_env_var_overrides_yaml_fallback(self):
        config_yaml = """
app:
  name: ${APP_NAME:Default Name}
  version: ${APP_VERSION:1.0.0}

server:
  port: ${SERVER_PORT:8000}
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "test_application.yml"
            path.write_text(config_yaml, encoding="utf-8")

            custom_env = {
                "APP_NAME": "Production SeniorLife",
                "SERVER_PORT": "5000",
            }
            with patch.dict(os.environ, custom_env, clear=True):
                cfg = load_config(path)
                self.assertEqual(cfg.app.name, "Production SeniorLife")
                self.assertEqual(cfg.server.port, 5000)

    def test_loads_llm_provider_sub_blocks(self):
        config_yaml = """
llm:
  provider: ${SENIORLIFE_LLM_PROVIDER:groq}
  groq:
    model: ${SENIORLIFE_LLM_MODEL:openai/gpt-oss-20b}
    api_key: ${GROQ_API_KEY:test-groq-key}
    temperature: 0.2
  openai:
    model: gpt-4o-custom
    api_key: test-openai-key
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "test_application.yml"
            path.write_text(config_yaml, encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                cfg = load_config(path)
                self.assertEqual(cfg.llm.provider, "groq")
                self.assertEqual(cfg.llm.groq.model, "openai/gpt-oss-20b")
                self.assertEqual(cfg.llm.groq.api_key, "test-groq-key")
                self.assertEqual(cfg.llm.groq.temperature, 0.2)
                self.assertEqual(cfg.llm.openai.model, "gpt-4o-custom")
                self.assertEqual(cfg.llm.openai.api_key, "test-openai-key")


if __name__ == "__main__":
    unittest.main()
