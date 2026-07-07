"""Autenticación de administradores: hashing de contraseñas y sesión web."""
from __future__ import annotations

import time

from fastapi import Request
from passlib.context import CryptContext
from sqlalchemy import select

from app.db import session_scope
from app.models import Admin

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")

# Hash señuelo: se verifica contra él cuando el usuario no existe, para que el
# coste temporal sea idéntico y no se pueda enumerar usuarios por 'timing'.
_DUMMY_HASH = _pwd.hash("teseo-dummy-password")

# Freno de fuerza bruta en memoria (por IP). Suficiente para un panel de admins;
# se reinicia con el proceso y no comparte estado entre workers.
_MAX_FAILS = 5
_WINDOW_SECONDS = 900  # 15 min
_failures: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password, password_hash)
    except Exception:
        return False


def _recent_failures(key: str, now: float) -> list[float]:
    xs = [t for t in _failures.get(key, []) if now - t < _WINDOW_SECONDS]
    if xs:
        _failures[key] = xs
    else:
        _failures.pop(key, None)
    return xs


def is_locked(key: str) -> bool:
    """True si ``key`` (p. ej. una IP) superó el máximo de intentos fallidos."""
    return len(_recent_failures(key, time.monotonic())) >= _MAX_FAILS


def register_failure(key: str) -> None:
    _failures.setdefault(key, []).append(time.monotonic())


def reset_failures(key: str) -> None:
    _failures.pop(key, None)


def authenticate(username: str, password: str) -> Admin | None:
    with session_scope() as session:
        admin = session.scalar(select(Admin).where(Admin.username == username))
        if admin is None:
            # Verificación señuelo para igualar el tiempo de respuesta.
            verify_password(password, _DUMMY_HASH)
            return None
        if verify_password(password, admin.password_hash):
            session.expunge(admin)
            return admin
    return None


def current_admin_id(request: Request) -> int | None:
    return request.session.get("admin_id")


def is_authenticated(request: Request) -> bool:
    return current_admin_id(request) is not None
