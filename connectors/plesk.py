"""Conector para servidores Plesk (STUB).

Registrado y seleccionable, pero sin reglas de descubrimiento todavía: las
reglas (qué constituye un origen de copia en Plesk — dominios, httpdocs, bases
de datos, configuración...) se definirán en una iteración posterior.
"""
from __future__ import annotations

from connectors import Ejecutar, VolumenDescubierto


class PleskConnector:
    TIPO = "plesk"
    NOMBRE = "Servidor Plesk"

    def descubrir(self, ejecutar: Ejecutar) -> list[VolumenDescubierto]:
        # TODO(iteración futura): definir el descubrimiento de orígenes de Plesk.
        raise NotImplementedError(
            "El conector Plesk aún no implementa el descubrimiento de orígenes."
        )

    def fuente_rsync(self, tipo_origen: str, ruta: str) -> tuple[str, list[str]]:
        return ruta, []

    def medir_tamano(self, ejecutar: Ejecutar, tipo_origen: str, ruta: str) -> int | None:
        return None  # el stub no mide tamaños todavía
