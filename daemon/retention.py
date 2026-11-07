"""Gestión de snapshots incrementales en el destino: enlace 'current' y rotación."""
from __future__ import annotations

import datetime as dt
import os
import shlex

from app.remote import SshError, SshTarget, connect, run


def set_current_symlink(destino: SshTarget, dest_root: str, snapshot_name: str) -> None:
    """Apunta ``<dest_root>/current`` al snapshot recién creado (enlace relativo)."""
    with connect(destino) as client:
        cmd = (
            f"cd {shlex.quote(dest_root)} && "
            f"ln -sfn {shlex.quote(snapshot_name)} current"
        )
        rc, _, err = run(client, cmd)
        if rc != 0:
            raise SshError(f"No se pudo actualizar el enlace 'current': {err}")


_TS_FMT = "%Y-%m-%d_%H%M%S"


def _snapshots_a_borrar(paths: list[str], dias: int, ahora: dt.datetime) -> list[str]:
    """Devuelve los snapshots más antiguos que ``dias`` días, conservando siempre
    el más reciente (nunca dejamos el origen sin ninguna copia).

    Los nombres son ``YYYY-MM-DD_HHMMSS`` (orden lexicográfico == cronológico).
    """
    dias = max(1, dias)
    corte = ahora - dt.timedelta(days=dias)
    ordenados = sorted(paths, reverse=True)  # más reciente primero
    borrar = []
    for i, path in enumerate(ordenados):
        if i == 0:
            continue  # nunca borrar el snapshot más reciente
        nombre = os.path.basename(path.rstrip("/"))
        try:
            cuando = dt.datetime.strptime(nombre, _TS_FMT)
        except ValueError:
            continue
        if cuando < corte:
            borrar.append(path)
    return borrar


# Prefijo con el que se apartan los snapshots caducados mientras se borran. No
# encaja con el patrón de fecha, así que el listado de snapshots deja de verlos.
_PREFIJO_BORRANDO = ".borrando_"


def comando_retencion(dest_root: str, snapshots: list[str]) -> str:
    """Orden de shell que aparta los ``snapshots`` y los borra descolgada.

    Borrar un snapshot con millones de enlaces duros tarda minutos u horas en
    discos mecánicos; hacerlo dentro del canal SSH superaba su timeout y la copia,
    correcta, acababa registrada como fallo. Por eso:
      1. ``mv`` a ``.borrando_<nombre>`` (instantáneo, mismo sistema de ficheros):
         el snapshot desaparece del listado al momento;
      2. ``rm -rf`` de todo ``.borrando_*`` en segundo plano con ``nohup`` (existe
         en Linux y macOS; ``setsid`` no existe en macOS). El patrón recoge también
         restos de borrados interrumpidos (p. ej. por un reinicio del destino).

    ``snapshots`` son rutas completas dentro de ``dest_root``.
    """
    root = dest_root.rstrip("/")
    ordenes = []
    for path in snapshots:
        nombre = os.path.basename(path.rstrip("/"))
        origen = shlex.quote(f"{root}/{nombre}")
        apartado = shlex.quote(f"{root}/{_PREFIJO_BORRANDO}{nombre}")
        ordenes.append(f"mv -- {origen} {apartado} 2>/dev/null")
    borrar = f"rm -rf -- {shlex.quote(root)}/{_PREFIJO_BORRANDO}*"
    ordenes.append(f"nohup sh -c {shlex.quote(borrar)} </dev/null >/dev/null 2>&1 &")
    return "; ".join(ordenes)


def apply_retention(destino: SshTarget, dest_root: str, dias: int) -> None:
    """Elimina los snapshots con más de ``dias`` días de antigüedad.

    Gracias a los hardlinks (``--link-dest``), borrar un snapshot solo libera los
    bloques que ningún otro snapshot referencia. Se ignora el enlace ``current``.
    El borrado físico corre descolgado (ver ``comando_retencion``), así que esta
    función vuelve en segundos aunque el snapshot sea enorme.
    """
    with connect(destino) as client:
        # Lista solo directorios con formato de timestamp.
        list_cmd = (
            f"find {shlex.quote(dest_root)} -maxdepth 1 -mindepth 1 -type d "
            r"-regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$' "
            "2>/dev/null | sort -r"
        )
        rc, out, _ = run(client, list_cmd)
        if rc != 0:
            return
        snapshots = [line for line in out.splitlines() if line.strip()]
        # Seguridad: solo borrar dentro de dest_root.
        prefijo = dest_root.rstrip("/") + "/"
        caducados = [p for p in _snapshots_a_borrar(snapshots, dias, dt.datetime.now())
                     if p.startswith(prefijo)]
        run(client, comando_retencion(dest_root, caducados))
