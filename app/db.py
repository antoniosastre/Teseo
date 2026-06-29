"""Gestión del engine y sesiones de SQLAlchemy.

El engine se crea de forma perezosa a partir de la configuración. Tanto la web
como el daemon importan ``get_engine`` / ``session_scope`` desde aquí para
compartir el mismo acceso a la BD.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import AppConfig, load_config

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def init_engine(config: AppConfig | None = None) -> Engine:
    """Inicializa (o reinicia) el engine global a partir de la configuración."""
    global _engine, _SessionLocal
    config = config or load_config()
    if config is None:
        raise RuntimeError("La aplicación no está configurada (falta config.ini).")
    _engine = create_engine(
        config.database.sqlalchemy_url(),
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def get_session() -> Session:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión transaccional: commit al salir, rollback ante excepción."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Fuerza la recreación del engine (p. ej. tras la instalación)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
