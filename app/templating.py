"""Configuración compartida de plantillas Jinja2."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def human_bytes(value: int | None) -> str:
    """Formatea bytes en unidades legibles (KB, MB, GB...)."""
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} EB"


def datetime_fmt(value) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M")


templates.env.filters["human_bytes"] = human_bytes
templates.env.filters["datetime_fmt"] = datetime_fmt
