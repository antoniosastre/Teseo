"""Cifrado simétrico de secretos almacenados en BD (contraseñas SSH, claves).

Se usa Fernet (AES-128 en CBC + HMAC) con la clave que vive en el fichero de
configuración, **nunca** en la base de datos. Así, una filtración de la BD no
expone las credenciales sin acceso al fichero de config.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


def generate_key() -> str:
    """Genera una clave Fernet nueva (base64 urlsafe de 32 bytes)."""
    return Fernet.generate_key().decode("ascii")


class SecretBox:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None or plaintext == "":
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            return None
