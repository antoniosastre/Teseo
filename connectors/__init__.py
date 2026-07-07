"""Sistema de conectores por tipo de dispositivo de origen.

Cada conector es una **estrategia** que define cómo descubrir los orígenes de
copia de un host y cómo tratarlos (qué copiar y con qué filtros de rsync). El
núcleo de la aplicación es agnóstico: solo conoce la interfaz ``Connector`` y
las estructuras declarativas que devuelve (``VolumenDescubierto`` /
``OrigenDescubierto``). Para añadir un dispositivo nuevo basta con implementar
un ``Connector`` y registrarlo — sin tocar modelos, routers ni daemon.

El tipo de conector (``TIPO``) se guarda en ``hosts_origen.tipo_conector``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

# Tipos de origen que puede producir un conector:
#   - "carpeta": una carpeta real del host (p. ej. una carpeta compartida).
#   - "config":  un *bundle* sintético (p. ej. la "Configuración" de Synology,
#                que agrupa todo lo que empieza por "@" en un volumen).
TIPOS_ORIGEN = ("carpeta", "config")

# Firma del ejecutor de comandos remotos que reciben los conectores. Se inyecta
# (en producción envuelve a ``app.remote.run`` sobre un cliente conectado; en
# tests se pasa un doble) para que el descubrimiento sea testeable sin SSH real.
Ejecutar = Callable[[str], "tuple[int, str, str]"]


@dataclass
class OrigenDescubierto:
    """Un origen de copia identificado por el conector."""

    nombre: str            # nombre visible (p. ej. "Configuración", "web")
    tipo: str              # uno de TIPOS_ORIGEN
    ruta: str              # ruta base en el host (p. ej. "/volume1/web")


@dataclass
class VolumenDescubierto:
    """Un volumen del host con los orígenes que contiene."""

    nombre: str                                  # p. ej. "volume1"
    origenes: list[OrigenDescubierto] = field(default_factory=list)


class Connector(Protocol):
    """Interfaz que implementa cada conector de dispositivo."""

    TIPO: str      # identificador estable, guardado en BD (p. ej. "synology")
    NOMBRE: str    # etiqueta legible para la UI (p. ej. "NAS Synology")

    def descubrir(self, ejecutar: Ejecutar) -> list[VolumenDescubierto]:
        """Explora el host y devuelve sus volúmenes y orígenes de copia."""
        ...

    def fuente_rsync(self, tipo_origen: str, ruta: str) -> tuple[str, list[str]]:
        """Traduce un origen a (ruta_fuente, filtros_rsync) para el comando rsync.

        ``filtros_rsync`` es una lista de flags ya lista para el comando (p. ej.
        ``["--include=@*", "--exclude=*"]`` para el bundle de configuración).
        """
        ...

    def medir_tamano(self, ejecutar: Ejecutar, tipo_origen: str, ruta: str) -> int | None:
        """Mide el tamaño en bytes del origen (``du``). Devuelve None si falla."""
        ...


# --- Registro de conectores --------------------------------------------------

_REGISTRO: dict[str, Connector] = {}


def registrar(conector: Connector) -> Connector:
    _REGISTRO[conector.TIPO] = conector
    return conector


def get_connector(tipo: str) -> Connector:
    try:
        return _REGISTRO[tipo]
    except KeyError:
        raise ValueError(f"Conector desconocido: {tipo!r}") from None


def conectores_disponibles() -> list[tuple[str, str]]:
    """Lista (tipo, nombre) para poblar el desplegable del wizard."""
    return [(c.TIPO, c.NOMBRE) for c in _REGISTRO.values()]


# Registro de los conectores incluidos. El import al final evita ciclos.
from connectors.plesk import PleskConnector  # noqa: E402
from connectors.synology import SynologyConnector  # noqa: E402

registrar(SynologyConnector())
registrar(PleskConnector())
