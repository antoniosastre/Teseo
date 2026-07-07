"""Conector para NAS Synology.

Reglas de descubrimiento:
  - Explora /volume1, /volume2, ... de forma incremental. Se detiene en el
    primer /volumeN que no exista.
  - En cada volumen:
      * Si hay entradas que empiezan por "@" (carpetas de sistema/aplicaciones),
        crea un origen "Configuración" (tipo "config") que copiará todo lo que
        empiece por "@".
      * Crea un origen (tipo "carpeta") por cada carpeta adicional (que no
        empiece por "@"): son las carpetas compartidas del NAS.

Tratamiento en rsync:
  - "carpeta": se copia la ruta tal cual.
  - "config":  se copia la raíz del volumen filtrando solo lo que empieza por
    "@" (``--include=@*`` + ``--exclude=*``).
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from connectors import Ejecutar, OrigenDescubierto, VolumenDescubierto

# Límite de volúmenes a explorar: cota de seguridad ante respuestas inesperadas.
_MAX_VOLUMENES = 64


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
    origenes: list[OrigenDescubierto] = []
    # Bundle "Configuración": existe si hay al menos una entrada que empiece por "@".
    if any(e.nombre.startswith("@") for e in entradas):
        origenes.append(OrigenDescubierto(nombre="Configuración", tipo="config", ruta=vol))
    # Un origen por cada carpeta compartida (directorio que no empiece por "@").
    for e in entradas:
        if e.es_dir and not e.nombre.startswith("@"):
            origenes.append(
                OrigenDescubierto(nombre=e.nombre, tipo="carpeta", ruta=f"{vol}/{e.nombre}")
            )
    return origenes


class SynologyConnector:
    TIPO = "synology"
    NOMBRE = "NAS Synology"

    def descubrir(self, ejecutar: Ejecutar) -> list[VolumenDescubierto]:
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
        if tipo_origen == "config":
            # Copiar solo lo que empieza por "@" en la raíz del volumen.
            return ruta, ["--include=@*", "--include=@*/**", "--exclude=*"]
        return ruta, []

    def medir_tamano(self, ejecutar, tipo_origen: str, ruta: str) -> int | None:
        q = shlex.quote(ruta)
        if tipo_origen == "config":
            # Suma del tamaño de todo lo que empieza por "@" en la raíz del volumen.
            cmd = f"du -scb {q}/@* 2>/dev/null | tail -1 | cut -f1"
        else:
            cmd = f"du -sb {q} 2>/dev/null | cut -f1"
        rc, out, _ = ejecutar(cmd)
        out = out.strip()
        return int(out) if rc == 0 and out.isdigit() else None
