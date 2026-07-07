"""Construcción del comando rsync y del layout de carpetas en el destino.

El comando se ejecuta **en el host origen** (vía SSH desde el controlador) y
empuja los datos directamente al destino. El controlador nunca toca los bytes.

Layout en el destino:
    <carpeta_base>/<host_origen>/<tipo_tarea>/<carpeta_origen>/<contenido>

donde <contenido> es:
  - espejo:      current/                 (réplica exacta, con --delete)
  - incremental: <timestamp>/  +  current -> último snapshot (vía --link-dest)
"""
from __future__ import annotations

import datetime as dt
import re
import shlex
from dataclasses import dataclass

# Flags base por defecto. -a (archivo), -z (compresión), progreso legible.
BASE_FLAGS = ["-a", "-z", "--info=progress2", "--stats"]


# Metacaracteres de shell que permitirían encadenar/inyectar comandos en un
# override. Un override es un "modo experto": debe ser UNA invocación de rsync,
# nunca una tubería, subshell ni redirección.
_SHELL_METACHARS = (";", "|", "&", "`", "$(", ">", "<", "\n", "\r")


def validate_override(comando: str) -> str | None:
    """Valida el override manual de comando. Devuelve mensaje de error o None.

    Reglas: debe empezar por ``rsync`` y no contener metacaracteres de shell que
    permitan encadenar comandos. No neutraliza los vectores propios de rsync
    (``-e``/``--rsync-path``), que quedan como riesgo aceptado del modo experto.
    """
    cmd = comando.strip()
    if not cmd:
        return None
    tokens = cmd.split()
    if tokens[0] != "rsync":
        return "El comando personalizado debe empezar por 'rsync'."
    if any(mc in cmd for mc in _SHELL_METACHARS):
        return "El comando personalizado no puede contener ; | & ` $( > < ni saltos de línea."
    return None


def sanitize_component(value: str) -> str:
    """Convierte una ruta/origen en un nombre de subcarpeta seguro y plano."""
    value = value.strip().strip("/")
    value = value.replace("/", "_")
    value = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    return value or "root"


def dest_task_dir(carpeta_base: str, host_nombre: str, tipo: str, carpeta_origen: str) -> str:
    """Directorio raíz de la tarea dentro del destino (sin la parte de contenido)."""
    base = carpeta_base.rstrip("/")
    return f"{base}/{sanitize_component(host_nombre)}/{tipo}/{sanitize_component(carpeta_origen)}"


@dataclass
class RsyncPlan:
    """Plan de ejecución de una copia concreta."""

    command: str                # comando rsync completo, listo para ejecutar en origen
    dest_root: str              # directorio de la tarea en el destino
    dest_target: str            # destino final de esta ejecución (current/ o snapshot/)
    snapshot_name: str | None   # nombre del snapshot si es incremental


def ssh_transport(destino_puerto: int, key_path: str | None) -> str:
    """Cadena del transporte para rsync (-e), con puerto y clave opcionales."""
    parts = ["ssh", "-p", str(destino_puerto), "-o", "StrictHostKeyChecking=accept-new"]
    if key_path:
        parts += ["-i", key_path]
    return " ".join(parts)


def build_plan(
    *,
    carpeta_origen: str,
    carpeta_base: str,
    host_nombre: str,
    tipo: str,
    destino_usuario: str,
    destino_host: str,
    destino_puerto: int = 22,
    key_path: str | None = None,
    extra_flags: str | None = None,
    delete: bool = True,
    timestamp: dt.datetime | None = None,
) -> RsyncPlan:
    """Genera el plan rsync para una ejecución (espejo o incremental)."""
    flags = list(BASE_FLAGS)
    dest_root = dest_task_dir(carpeta_base, host_nombre, tipo, carpeta_origen)

    if tipo == "incremental":
        ts = (timestamp or dt.datetime.now()).strftime("%Y-%m-%d_%H%M%S")
        snapshot_name = ts
        dest_target = f"{dest_root}/{ts}"
        # link-dest relativo: enlaza ficheros sin cambios al último 'current'.
        flags.append("--link-dest=../current")
    else:  # espejo
        snapshot_name = None
        dest_target = f"{dest_root}/current"
        if delete:
            flags.append("--delete")

    # shlex.split respeta las comillas del usuario; volvemos a citar cada token
    # con shlex.quote antes de unir, para que ningún metacarácter quede a merced
    # del shell remoto (evita inyección vía rsync_extra, p. ej. "--rsh=$(...)").
    extra = [shlex.quote(t) for t in shlex.split(extra_flags)] if extra_flags else []

    src = carpeta_origen.rstrip("/") + "/"
    remote = f"{destino_usuario}@{destino_host}:{dest_target}/"
    transport = ssh_transport(destino_puerto, key_path)

    # Montamos el string respetando que el transporte va como un único argumento.
    tokens = (
        ["rsync"]
        + flags
        + ["-e", shlex.quote(transport)]
        + extra
        + [shlex.quote(src), shlex.quote(remote)]
    )
    command = " ".join(tokens)

    return RsyncPlan(
        command=command,
        dest_root=dest_root,
        dest_target=dest_target,
        snapshot_name=snapshot_name,
    )


def preview_command(
    *,
    carpeta_origen: str,
    carpeta_base: str,
    host_nombre: str,
    tipo: str,
    destino_usuario: str,
    destino_host: str,
    destino_puerto: int = 22,
    extra_flags: str | None = None,
) -> str:
    """Comando representativo para mostrar en 'opciones avanzadas' del formulario."""
    plan = build_plan(
        carpeta_origen=carpeta_origen,
        carpeta_base=carpeta_base,
        host_nombre=host_nombre,
        tipo=tipo,
        destino_usuario=destino_usuario,
        destino_host=destino_host,
        destino_puerto=destino_puerto,
        key_path="~/.ssh/teseo_taskkey",
        extra_flags=extra_flags,
    )
    return plan.command
