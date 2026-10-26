"""Database engine and sessions (SQLAlchemy 2, synchronous, psycopg 3).

Synchronous on purpose: the document graph, OCR and the LangGraph PostgreSQL checkpointer
are all synchronous, and FastAPI runs plain `def` endpoints in a worker thread. (The async
mode would also need the greenlet package.)
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from .settings import with_driver


def make_engine(url: str, pool_size: int = 5, max_overflow: int = 10, echo: bool = False) -> Engine:
    """url may name any PostgreSQL driver (postgresql+asyncpg://... is fine); psycopg is used.
    The API takes pool_size, max_overflow and echo from config/application.yml (database:)."""
    return create_engine(
        with_driver(url, "psycopg"),
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
        pool_pre_ping=True,
        hide_parameters=True,  # SQL errors and logs (also with echo) never show the values sent
    )


def make_sessions(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
