"""Lógica del asistente de instalación.

Prueba la conexión MySQL, crea la base de datos y las tablas, escribe el fichero
de configuración y da de alta el primer administrador.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text

from app.config import DatabaseConfig, SmtpConfig, write_config
from app.crypto import generate_key
from app.db import init_engine, reset_engine, session_scope
from app.models import Admin, Base


def test_connection(db: DatabaseConfig) -> tuple[bool, str]:
    """Comprueba que se puede conectar al servidor MySQL (sin exigir la BD)."""
    try:
        engine = create_engine(db.server_url(), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True, "Conexión correcta."
    except Exception as exc:  # noqa: BLE001 - mostramos el error al usuario
        return False, f"No se pudo conectar: {exc}"


def create_database(db: DatabaseConfig) -> None:
    engine = create_engine(db.server_url())
    with engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{db.name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.commit()
    engine.dispose()


def run_install(
    db: DatabaseConfig,
    admin_username: str,
    admin_password: str,
    admin_email: str,
    smtp: SmtpConfig | None = None,
) -> None:
    """Ejecuta la instalación completa de forma idempotente."""
    from app.auth import hash_password

    # 1) Crear la base de datos si no existe.
    create_database(db)

    # 2) Escribir la configuración (genera claves de sesión y de cifrado).
    write_config(
        database=db,
        secret_key=generate_key(),
        encryption_key=generate_key(),
        smtp=smtp,
    )

    # 3) Crear tablas con el engine ya apuntando a la BD definitiva.
    reset_engine()
    engine = init_engine()
    Base.metadata.create_all(engine)

    # 4) Crear el primer administrador.
    with session_scope() as session:
        if session.query(Admin).count() == 0:
            session.add(
                Admin(
                    username=admin_username,
                    password_hash=hash_password(admin_password),
                    email=admin_email or None,
                )
            )
