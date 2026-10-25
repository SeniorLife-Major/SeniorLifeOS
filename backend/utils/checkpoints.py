"""Encrypted LangGraph checkpoints (Decision #27 follow-up, plan part 2).

A run that pauses for review is saved as a checkpoint. That saved state holds page text,
names and, before review, full ID numbers, so every checkpoint is encrypted with AES
(EAX mode: encrypted and tamper-checked) using SENIORLIFE_CHECKPOINT_KEY, 64 hex
characters (32 bytes). Make one with:

    python -c "import secrets; print(secrets.token_hex(32))"

- No key, or a malformed one: no checkpointer can be made, so a run that would pause
  cannot start.
- A checkpoint saved without encryption is refused when read (LangGraph's own
  serializer would quietly accept it).
- A checkpoint saved with another key cannot be read.
- forget_run() deletes a run's checkpoints once it has ended.
"""

from __future__ import annotations

import os
import re
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

from .env import find_env_file, load_env_file

KEY_NAME = "SENIORLIFE_CHECKPOINT_KEY"
KEY_FORMAT = re.compile(r"[0-9a-fA-F]{64}")


class CheckpointKeyError(RuntimeError):
    """The checkpoint key is missing or malformed. The message never contains the key."""


class StrictEncryptedSerializer(EncryptedSerializer):
    """Like LangGraph's EncryptedSerializer, but data saved without encryption is refused."""

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        kind = data[0]
        if "+" not in kind:
            raise ValueError("Refusing an unencrypted checkpoint")
        return super().loads_typed(data)


def checkpoint_key() -> bytes:
    load_env_file(find_env_file())
    raw = os.environ.get(KEY_NAME, "").strip()
    if not raw:
        raise CheckpointKeyError(f"{KEY_NAME} is not set; runs that pause for review need it")
    if not KEY_FORMAT.fullmatch(raw):
        raise CheckpointKeyError(f"{KEY_NAME} must be 64 hex characters (32 bytes)")
    return bytes.fromhex(raw)


def encrypted_serde(key: bytes | None = None) -> StrictEncryptedSerializer:
    plain = EncryptedSerializer.from_pycryptodome_aes(key=key or checkpoint_key())
    return StrictEncryptedSerializer(plain.cipher, plain.serde)


def make_checkpointer(key: bytes | None = None) -> InMemorySaver:
    """An in-memory checkpointer that encrypts everything it saves. (A PostgreSQL one comes
    with storage; it takes the same serializer.)"""
    return InMemorySaver(serde=encrypted_serde(key))


def is_encrypted(checkpointer: Any) -> bool:
    return isinstance(getattr(checkpointer, "serde", None), StrictEncryptedSerializer)


def forget_run(checkpointer: Any, thread_id: str) -> None:
    """Delete every checkpoint of a run. Call it once the run has ended (not while paused)."""
    checkpointer.delete_thread(thread_id)


def run_and_forget(graph: Any, graph_input: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Invoke the graph; if the run ended instead of pausing, delete its checkpoints."""
    result = graph.invoke(graph_input, config)
    if "__interrupt__" not in result:
        forget_run(graph.checkpointer, config["configurable"]["thread_id"])
    return result
