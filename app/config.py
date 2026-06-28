"""Carga y validación del fichero de configuración.

El fichero `config.ini` guarda la conexión a MySQL, la clave de cifrado de
secretos y la configuración SMTP. Si el fichero no existe (o está incompleto),
la aplicación se considera **no instalada** y redirige al asistente.
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

# Permite sobreescribir la ruta del config por variable de entorno (útil en tests
# y despliegues). Por defecto se busca junto a la raíz del proyecto.
CONFIG_PATH = Path(os.environ.get("TESEO_CONFIG", Path(__file__).resolve().parent.parent / "config.ini"))


@dataclass
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    name: str

    def sqlalchemy_url(self) -> str:
        from urllib.parse import quote_plus

        return (
            f"mysql+pymysql://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{quote_plus(self.name)}?charset=utf8mb4"
        )

    def server_url(self) -> str:
        """URL sin nombre de BD, para crear la base durante la instalación."""
        from urllib.parse import quote_plus

        return (
            f"mysql+pymysql://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/?charset=utf8mb4"
        )


@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    sender: str = ""
    use_tls: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.sender)


@dataclass
class AppConfig:
    database: DatabaseConfig
    secret_key: str          # clave de sesión web
    encryption_key: str      # clave Fernet para cifrar secretos en BD
    smtp: SmtpConfig

    @property
    def configured(self) -> bool:
        return bool(self.database and self.encryption_key)


def config_exists() -> bool:
    return CONFIG_PATH.exists()


def load_config(path: Path | None = None) -> AppConfig | None:
    """Devuelve la configuración cargada, o ``None`` si la app no está instalada."""
    path = path or CONFIG_PATH
    if not path.exists():
        return None

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    if "database" not in parser or "security" not in parser:
        return None

    db = parser["database"]
    database = DatabaseConfig(
        host=db.get("host", "localhost"),
        port=db.getint("port", 3306),
        user=db.get("user", ""),
        password=db.get("password", ""),
        name=db.get("name", ""),
    )

    sec = parser["security"]
    secret_key = sec.get("secret_key", "")
    encryption_key = sec.get("encryption_key", "")

    smtp_section = parser["smtp"] if "smtp" in parser else {}
    smtp = SmtpConfig(
        host=smtp_section.get("host", "") if smtp_section else "",
        port=int(smtp_section.get("port", 587)) if smtp_section else 587,
        user=smtp_section.get("user", "") if smtp_section else "",
        password=smtp_section.get("password", "") if smtp_section else "",
        sender=smtp_section.get("sender", "") if smtp_section else "",
        use_tls=(smtp_section.get("use_tls", "true").lower() == "true") if smtp_section else True,
    )

    return AppConfig(database=database, secret_key=secret_key, encryption_key=encryption_key, smtp=smtp)


def write_config(
    database: DatabaseConfig,
    secret_key: str,
    encryption_key: str,
    smtp: SmtpConfig | None = None,
    path: Path | None = None,
) -> None:
    """Persiste la configuración en disco con permisos restrictivos (0600)."""
    path = path or CONFIG_PATH
    parser = configparser.ConfigParser()
    parser["database"] = {
        "host": database.host,
        "port": str(database.port),
        "user": database.user,
        "password": database.password,
        "name": database.name,
    }
    parser["security"] = {
        "secret_key": secret_key,
        "encryption_key": encryption_key,
    }
    if smtp and smtp.host:
        parser["smtp"] = {
            "host": smtp.host,
            "port": str(smtp.port),
            "user": smtp.user,
            "password": smtp.password,
            "sender": smtp.sender,
            "use_tls": "true" if smtp.use_tls else "false",
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    # Escribe primero, luego restringe permisos para no dejar secretos legibles.
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
