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


def dest_task_dir(
    carpeta_base: str, host_nombre: str, volumen_nombre: str, origen_nombre: str, tipo: str
) -> str:
    """Directorio raíz de la tarea en el destino: base/host/volumen/origen/tipo."""
    base = carpeta_base.rstrip("/")
    return (
        f"{base}/{sanitize_component(host_nombre)}/{sanitize_component(volumen_nombre)}"
        f"/{sanitize_component(origen_nombre)}/{tipo}"
    )


@dataclass
class RsyncPlan:
    """Plan de ejecución de una copia concreta."""

    command: str                # comando rsync completo, listo para ejecutar en origen
    dest_root: str              # directorio de la tarea en el destino
    dest_target: str            # destino final de esta ejecución (current/ o snapshot/)
    snapshot_name: str | None   # nombre del snapshot si es incremental


def ssh_transport(destino_puerto: int, key_path: str | None) -> str:
    """Cadena del transporte para rsync (-e), con puerto y clave opcionales.

    ``BatchMode=yes`` impide que el ssh interno degrade a contraseña/keyboard-interactive
    (sin terminal fallaría con un confuso "Permission denied, please try again."); con
    clave se fija ``IdentitiesOnly=yes`` para ofrecer SOLO esa clave (no agotar MaxAuthTries
    con claves por defecto ni del agente).
    """
    parts = ["ssh", "-p", str(destino_puerto),
             "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes"]
    if key_path:
        parts += ["-i", key_path, "-o", "IdentitiesOnly=yes"]
    return " ".join(parts)


def build_plan(
    *,
    ruta_origen: str,
    carpeta_base: str,
    host_nombre: str,
    volumen_nombre: str,
    origen_nombre: str,
    tipo: str,
    destino_usuario: str,
    destino_host: str,
    destino_puerto: int = 22,
    key_path: str | None = None,
    extra_flags: str | None = None,
    filtros: list[str] | None = None,
    delete: bool = True,
    timestamp: dt.datetime | None = None,
) -> RsyncPlan:
    """Genera el plan rsync para una ejecución (espejo o incremental).

    ``ruta_origen`` es la ruta a copiar en el host; ``filtros`` son flags de
    include/exclude que aporta el conector (p. ej. el bundle @ de Synology).
    """
    flags = list(BASE_FLAGS)
    dest_root = dest_task_dir(carpeta_base, host_nombre, volumen_nombre, origen_nombre, tipo)

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

    # Citamos cada token (filtros del conector + extras del usuario) con
    # shlex.quote antes de unir: ningún metacarácter queda a merced del shell
    # remoto (evita inyección, p. ej. "--rsh=$(...)").
    filtro_tokens = [shlex.quote(f) for f in (filtros or [])]
    extra = [shlex.quote(t) for t in shlex.split(extra_flags)] if extra_flags else []

    src = ruta_origen.rstrip("/") + "/"
    remote = f"{destino_usuario}@{destino_host}:{dest_target}/"
    transport = ssh_transport(destino_puerto, key_path)

    # Montamos el string respetando que el transporte va como un único argumento.
    tokens = (
        ["rsync"]
        + flags
        + ["-e", shlex.quote(transport)]
        + filtro_tokens
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
    ruta_origen: str,
    carpeta_base: str,
    host_nombre: str,
    volumen_nombre: str,
    origen_nombre: str,
    tipo: str,
    destino_usuario: str,
    destino_host: str,
    destino_puerto: int = 22,
    extra_flags: str | None = None,
    filtros: list[str] | None = None,
) -> str:
    """Comando representativo para mostrar en 'opciones avanzadas' del formulario."""
    plan = build_plan(
        ruta_origen=ruta_origen,
        carpeta_base=carpeta_base,
        host_nombre=host_nombre,
        volumen_nombre=volumen_nombre,
        origen_nombre=origen_nombre,
        tipo=tipo,
        destino_usuario=destino_usuario,
        destino_host=destino_host,
        destino_puerto=destino_puerto,
        key_path="~/.ssh/teseo_taskkey",
        extra_flags=extra_flags,
        filtros=filtros,
    )
    return plan.command
