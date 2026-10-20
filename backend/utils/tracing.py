"""LangSmith tracing that keeps private data on this machine.

Tracing is on only when LANGSMITH_TRACING=true and LANGSMITH_API_KEY are set (shell
or .env; the older LANGCHAIN_* names count too). Every traced run - graph nodes, LLM calls, metadata and error text - is
passed through `safe_view` before it is sent. It is an allowlist: only keys in
SAFE_KEYS keep their values (node path, status, flags, confidence, line numbers...).
Every other value becomes "[hidden]", so names, ID numbers, dates of birth, OCR text
and LLM prompts never leave the machine. Kept strings are also run through
`privacy.redact` in case an ID number slipped into a message.

The safe client is installed when a graph is built (and again after .env is loaded),
so it also covers tracing switched on by environment variables alone.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any

from .env import find_env_file, load_env_file
from .privacy import redact

HIDDEN = "[hidden]"

# Keys whose values are safe to send: workflow facts, never personal data.
SAFE_KEYS = frozenset({
    # graph control and results
    "events", "status", "route", "retry_count", "ocr_attempts", "review_flags", "warnings", "errors",
    "document_type", "claimed_type", "document_id", "case_id", "scheme_id", "jurisdiction",
    "case_version", "expected_case_version", "use_llm", "use_interrupt", "simulate_ocr_failures",
    "action", "corrected_by_user", "demo_only", "type",
    # where a value came from and how sure OCR was (not the value itself)
    "confidence", "source_ref", "page", "line", "box", "engine", "quality", "blurry", "sharpness",
    "cleanup", "corrections", "mime_type", "size_bytes",
    # LangGraph / LangSmith run metadata
    "thread_id", "checkpoint_ns", "run_attempt", "revision_id",
})
SAFE_KEY_PREFIXES = ("langgraph_", "ls_")
# Never sent, even their keys: the vault's hash -> document map uses hashes as keys.
NEVER_KEYS = frozenset({"vault_identifier_hashes", "identifier_hashes"})
SCALARS = (str, int, float, bool, type(None))


def is_safe_key(key: Any) -> bool:
    return isinstance(key, str) and (key in SAFE_KEYS or key.startswith(SAFE_KEY_PREFIXES))


def safe_view(data: Any) -> Any:
    """A copy of traced data with every non-allowlisted value replaced by "[hidden]"."""
    return _scrub(data, None)


def _scrub(value: Any, key: Any) -> Any:
    if key in NEVER_KEYS:
        return HIDDEN
    if isinstance(value, dict):
        return {k: _scrub(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(item, key) for item in value]  # items inherit their list's key
    if key == "error" and isinstance(value, str):
        return redact(value)[0]  # error text: keep it readable, hide ID-like numbers
    if is_safe_key(key) and isinstance(value, SCALARS):
        return redact(value)[0] if isinstance(value, str) else value
    return HIDDEN


# LangSmith switches tracing on with any of these, under either name prefix.
FLAG_NAMES = ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING")
KEY_NAMES = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")

_lock = threading.Lock()
_installed_for: str | None = None  # fingerprint of the API key the safe client was made with


def tracing_requested() -> bool:
    return any(os.environ.get(name, "").strip().lower() == "true" for name in FLAG_NAMES)


def api_key() -> str:
    return next((os.environ[name].strip() for name in KEY_NAMES if os.environ.get(name, "").strip()), "")


def install_safe_client() -> None:
    """If tracing is switched on, make LangSmith's shared client the masked one.

    Cheap to call again; a new client is made only when the API key changed.
    """
    global _installed_for
    if not tracing_requested():
        return  # nothing is traced, so nothing to protect (and no "missing key" warning)
    import langsmith

    fingerprint = hashlib.sha256(api_key().encode()).hexdigest()
    with _lock:
        if _installed_for == fingerprint:
            return
        langsmith.configure(client=langsmith.Client(anonymizer=safe_view))
        _installed_for = fingerprint


def tracing_enabled() -> bool:
    return tracing_requested() and bool(api_key())


def setup_tracing() -> bool:
    """Load .env, install the masked client, and say whether tracing is on."""
    load_env_file(find_env_file())
    install_safe_client()
    return tracing_enabled()
