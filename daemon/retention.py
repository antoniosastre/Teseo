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


def apply_retention(destino: SshTarget, dest_root: str, dias: int) -> None:
    """Elimina los snapshots con más de ``dias`` días de antigüedad.

    Gracias a los hardlinks (``--link-dest``), borrar un snapshot solo libera los
    bloques que ningún otro snapshot referencia. Se ignora el enlace ``current``.
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
        for path in _snapshots_a_borrar(snapshots, dias, dt.datetime.now()):
            # Seguridad: solo borrar dentro de dest_root.
            if not path.startswith(dest_root.rstrip("/") + "/"):
                continue
            run(client, f"rm -rf {shlex.quote(path)}")
