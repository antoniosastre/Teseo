"""Autenticación de administradores: hashing de contraseñas y sesión web."""
from __future__ import annotations

from fastapi import Request
from passlib.context import CryptContext
from sqlalchemy import select

from app.db import session_scope
from app.models import Admin

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password, password_hash)
    except Exception:
        return False


def authenticate(username: str, password: str) -> Admin | None:
    with session_scope() as session:
        admin = session.scalar(select(Admin).where(Admin.username == username))
        if admin and verify_password(password, admin.password_hash):
            session.expunge(admin)
            return admin
    return None


def current_admin_id(request: Request) -> int | None:
    return request.session.get("admin_id")


def is_authenticated(request: Request) -> bool:
    return current_admin_id(request) is not None
