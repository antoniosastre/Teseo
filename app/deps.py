"""Dependencias comunes de FastAPI (autenticación, acceso a secretos)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.config import load_config
from app.crypto import SecretBox


class RedirectException(Exception):
    """Excepción que el middleware traduce en una redirección HTTP."""

    def __init__(self, location: str):
        self.location = location


def require_login(request: Request):
    """Dependencia que exige sesión activa; redirige a /login si no la hay."""
    if not request.session.get("admin_id"):
        raise RedirectException("/login")
    return request.session["admin_id"]


def get_secret_box() -> SecretBox:
    config = load_config()
    if config is None:
        raise RuntimeError("Configuración no disponible.")
    return SecretBox(config.encryption_key)


__all__ = ["RedirectException", "require_login", "get_secret_box", "RedirectResponse"]
