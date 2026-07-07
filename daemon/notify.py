"""Avisos por email (SMTP) ante fallos de copia o orígenes inaccesibles."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from sqlalchemy import select

from app.config import load_config
from app.db import session_scope
from app.models import Admin


def _recipients() -> list[str]:
    with session_scope() as session:
        return [a.email for a in session.scalars(select(Admin)) if a.email]


def send_email(subject: str, body: str) -> bool:
    config = load_config()
    if config is None or not config.smtp.enabled:
        return False
    recipients = _recipients()
    if not recipients:
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[Teseo] {subject}"
    msg["From"] = config.smtp.sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        with smtplib.SMTP(config.smtp.host, config.smtp.port, timeout=20) as server:
            if config.smtp.use_tls:
                server.starttls()
            if config.smtp.user:
                server.login(config.smtp.user, config.smtp.password)
            server.send_message(msg)
        return True
    except Exception:  # noqa: BLE001 - un fallo de email no debe romper la copia
        return False


def notify_failure(host_nombre: str, carpeta: str, detalle: str) -> None:
    send_email(
        subject=f"Fallo de copia: {host_nombre}:{carpeta}",
        body=(
            f"La copia de seguridad de {host_nombre}:{carpeta} ha fallado.\n\n"
            f"Detalle:\n{detalle}\n"
        ),
    )


def notify_unreachable(host_nombre: str) -> None:
    send_email(
        subject=f"Origen inaccesible: {host_nombre}",
        body=f"El host origen {host_nombre} no responde por SSH.\n",
    )


def notify_orphan(host_nombre: str, origen_nombre: str) -> None:
    send_email(
        subject=f"Origen desaparecido: {host_nombre}:{origen_nombre}",
        body=(
            f"El origen '{origen_nombre}' del host '{host_nombre}' ha DESAPARECIDO "
            "en la última exploración, pero tenía tareas de copia configuradas "
            "(ahora huérfanas).\n\n"
            "Esto puede significar que se ha eliminado o renombrado un origen de "
            "datos. Revísalo: si es intencionado, elimina sus tareas; si no, "
            "restaura el origen. Teseo NO ha borrado nada automáticamente.\n"
        ),
    )
