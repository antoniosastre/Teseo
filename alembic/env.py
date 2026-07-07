"""Entorno de Alembic para Teseo.

La URL de la BD se toma de la configuración de Teseo (config.ini / TESEO_CONFIG),
no del alembic.ini. El metadata objetivo es el de los modelos ORM, de modo que
``alembic revision --autogenerate`` compara contra ``app/models.py``.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import load_config
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    cfg = load_config()
    if cfg is None:
        raise RuntimeError(
            "Teseo no está configurado: falta config.ini (define TESEO_CONFIG)."
        )
    return cfg.database.sqlalchemy_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
