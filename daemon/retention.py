"""Gestión de snapshots incrementales en el destino: enlace 'current' y rotación."""
from __future__ import annotations

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


def apply_retention(destino: SshTarget, dest_root: str, keep: int) -> None:
    """Conserva los ``keep`` snapshots más recientes y elimina el resto.

    Los snapshots son carpetas con nombre ``YYYY-MM-DD_HHMMSS`` (orden lexicográfico
    == cronológico). Se ignora el enlace ``current``.
    """
    keep = max(1, keep)
    with connect(destino) as client:
        # Lista solo directorios con formato de timestamp, ordenados desc.
        list_cmd = (
            f"find {shlex.quote(dest_root)} -maxdepth 1 -mindepth 1 -type d "
            r"-regextype posix-extended -regex '.*/[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$' "
            "2>/dev/null | sort -r"
        )
        rc, out, _ = run(client, list_cmd)
        if rc != 0:
            return
        snapshots = [line for line in out.splitlines() if line.strip()]
        sobrantes = snapshots[keep:]
        for path in sobrantes:
            # Seguridad: solo borrar dentro de dest_root.
            if not path.startswith(dest_root.rstrip("/") + "/"):
                continue
            run(client, f"rm -rf {shlex.quote(path)}")
