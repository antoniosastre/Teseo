"""Acceso a los ajustes globales (tabla ``ajustes``), con valores por defecto.

Los ajustes son pares clave→valor editables desde la UI (sección Ajustes) y
consumidos por el daemon (p. ej. el intervalo del analizador).
"""
from __future__ import annotations

from sqlalchemy import select

from app.models import Ajuste

# Claves conocidas y sus valores por defecto.
ANALIZADOR_INTERVALO_HORAS = "analizador_intervalo_horas"
ANALIZADOR_RUN_NOW = "analizador_run_now"

_DEFAULTS = {
    ANALIZADOR_INTERVALO_HORAS: "24",
    ANALIZADOR_RUN_NOW: "0",
}


def get_ajuste(session, clave: str) -> str:
    row = session.get(Ajuste, clave)
    if row is not None:
        return row.valor
    return _DEFAULTS.get(clave, "")


def set_ajuste(session, clave: str, valor: str) -> None:
    row = session.get(Ajuste, clave)
    if row is None:
        session.add(Ajuste(clave=clave, valor=valor))
    else:
        row.valor = valor


def get_int(session, clave: str, minimo: int = 1) -> int:
    try:
        return max(minimo, int(get_ajuste(session, clave)))
    except (ValueError, TypeError):
        return max(minimo, int(_DEFAULTS.get(clave, minimo)))


def intervalo_analizador_horas(session) -> int:
    return get_int(session, ANALIZADOR_INTERVALO_HORAS, minimo=1)


def analizador_run_now(session) -> bool:
    return get_ajuste(session, ANALIZADOR_RUN_NOW) == "1"


def marcar_analizador_run_now(session, valor: bool) -> None:
    set_ajuste(session, ANALIZADOR_RUN_NOW, "1" if valor else "0")
