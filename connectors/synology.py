"""Conector para NAS Synology.

Reglas de descubrimiento:
  - Explora /volume1, /volume2, ... de forma incremental. Se detiene en el
    primer /volumeN que no exista.
  - En cada volumen crea un origen (tipo "carpeta") por cada carpeta compartida
    (directorio que NO empiece por "@"). Las carpetas "@" (sistema/aplicaciones)
    se ignoran: la configuración del DSM se respalda con la herramienta nativa
    de Synology (Panel de control → Copia de seguridad de configuración), que
    lo hace de forma coherente; un rsync de esas carpetas internas no lo es.
    (Decisión del usuario, 070926. Antes existía un bundle sintético
    "Configuración" tipo "config"; el manejo de ese tipo se conserva más abajo
    como LEGADO para orígenes que aún existan en BD.)
  - La carpeta compartida "Teseo" también se ignora: por convención es el
    DESTINO donde otros orígenes depositan sus copias — descubrirla como
    origen crearía copias de copias (decisión del usuario, 071026).

Tratamiento en rsync:
  - "carpeta": se copia la ruta tal cual, excluyendo metadatos y transitorios
    (_EXCLUSIONES: papelera #recycle, snapshots, .DS_Store, Thumbs.db, locks…).
  - "config" (legado): raíz del volumen filtrando solo lo que empieza por "@".
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from connectors import Ejecutar, OpcionDescubrimiento, OrigenDescubierto, VolumenDescubierto

# Límite de volúmenes a explorar: cota de seguridad ante respuestas inesperadas.
_MAX_VOLUMENES = 64

# Carpeta compartida que por convención actúa de DESTINO de copias en un
# Synology: nunca se descubre como origen (evita copias de copias).
_CARPETA_DESTINO = "teseo"  # comparación sin mayúsculas

# Exclusiones de rsync para TODA copia Synology: metadatos de escritorio y
# transitorios, nunca datos reales. rsync aplica los filtros por orden y gana
# el primer match, así que van ANTES de cualquier --include.
_EXCLUSIONES = [
    "--exclude=@eaDir",       # índices/miniaturas internos de Synology
    "--exclude=#recycle",     # papelera de reciclaje (raíz de cada compartida)
    "--exclude=#snapshot",    # snapshots btrfs visibles (copiarlos multiplica la copia)
    "--exclude=.DS_Store",    # metadatos del Finder (macOS)
    "--exclude=._*",          # ficheros AppleDouble del Finder en volúmenes de red
    "--exclude=Thumbs.db",    # miniaturas del Explorador de Windows
    "--exclude=desktop.ini",  # metadatos del Explorador de Windows
    "--exclude=~$*",          # bloqueos temporales de Office (documento abierto)
    "--exclude=*.lock",       # ficheros de bloqueo genéricos (transitorios)
    "--exclude=.TemporaryItems",  # carpeta temporal que macOS crea en shares SMB
]


@dataclass
class _Entrada:
    nombre: str
    es_dir: bool


def _parse_ls_ap(salida: str) -> list[_Entrada]:
    """Parsea la salida de ``ls -1Ap`` (los directorios acaban en '/')."""
    entradas: list[_Entrada] = []
    for linea in salida.splitlines():
        nombre = linea.rstrip("\n")
        if not nombre:
            continue
        es_dir = nombre.endswith("/")
        entradas.append(_Entrada(nombre=nombre.rstrip("/"), es_dir=es_dir))
    return entradas


def _volumen_existe(ejecutar: Ejecutar, ruta: str) -> bool:
    rc, out, _ = ejecutar(f"test -d {shlex.quote(ruta)} && echo ok")
    return rc == 0 and "ok" in out


def _listar_entradas(ejecutar: Ejecutar, ruta: str) -> list[_Entrada]:
    rc, out, _ = ejecutar(f"ls -1Ap {shlex.quote(ruta)}")
    if rc != 0:
        return []
    return _parse_ls_ap(out)


def _origenes_de_volumen(vol: str, entradas: list[_Entrada]) -> list[OrigenDescubierto]:
    # Un origen por cada carpeta compartida. Se ignoran: las "@" (sistema; la
    # configuración se respalda con la herramienta nativa del DSM, no con rsync)
    # y la carpeta destino de copias "Teseo" (evita copias de copias).
    return [
        OrigenDescubierto(nombre=e.nombre, tipo="carpeta", ruta=f"{vol}/{e.nombre}")
        for e in entradas
        if e.es_dir
        and not e.nombre.startswith("@")
        and e.nombre.lower() != _CARPETA_DESTINO
    ]


class SynologyConnector:
    TIPO = "synology"
    NOMBRE = "NAS Synology"

    def opciones_descubrimiento(self) -> list[OpcionDescubrimiento]:
        return []

    def descubrir(self, ejecutar: Ejecutar, opciones: dict | None = None) -> list[VolumenDescubierto]:
        volumenes: list[VolumenDescubierto] = []
        for i in range(1, _MAX_VOLUMENES + 1):
            vol = f"/volume{i}"
            if not _volumen_existe(ejecutar, vol):
                break  # se detiene en el primer volumen inexistente
            entradas = _listar_entradas(ejecutar, vol)
            volumenes.append(
                VolumenDescubierto(nombre=f"volume{i}", origenes=_origenes_de_volumen(vol, entradas))
            )
        return volumenes

    def fuente_rsync(self, tipo_origen: str, ruta: str) -> tuple[str, list[str]]:
        # Las exclusiones van PRIMERO para que ganen a cualquier --include
        # posterior (rsync aplica los filtros por orden, primer match).
        if tipo_origen == "config":
            # LEGADO: orígenes "Configuración" creados antes del 070926 que sigan en BD.
            # Copia solo lo que empieza por "@" en la raíz del volumen (menos exclusiones).
            return ruta, [*_EXCLUSIONES, "--include=@*", "--include=@*/**", "--exclude=*"]
        return ruta, list(_EXCLUSIONES)

    def medir_tamano(self, ejecutar, tipo_origen: str, ruta: str) -> int | None:
        q = shlex.quote(ruta)
        if tipo_origen == "config":
            # LEGADO (ver fuente_rsync): tamaño de todo lo "@" en la raíz del volumen.
            cmd = f"du -scb {q}/@* 2>/dev/null | tail -1 | cut -f1"
        else:
            cmd = f"du -sb {q} 2>/dev/null | cut -f1"
        rc, out, _ = ejecutar(cmd)
        out = out.strip()
        return int(out) if rc == 0 and out.isdigit() else None
