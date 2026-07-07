"""Ejecución de una tarea de backup: rsync en el origen empujando al destino.

El controlador abre SSH al **host origen** y lanza ahí el rsync. El progreso se
lee en streaming (``--info=progress2``) y se persiste en la BD para que el panel
lo muestre en vivo. La gestión de snapshots/retención y el enlace ``current`` se
realizan por SSH al **destino**.
"""
from __future__ import annotations

import datetime as dt
import re
import shlex

from app.crypto import SecretBox
from app.db import session_scope
from app.models import Destino, Ejecucion, Origen, Tarea
from app.remote import SshError, SshTarget, connect, run
from app.rsync_cmd import build_plan
from app.services import ssh_target_for_destino, ssh_target_for_host
from connectors import get_connector
from daemon.keyprov import ensure_trust
from daemon.notify import notify_failure
from daemon.retention import apply_retention, set_current_symlink

_PCT_RE = re.compile(rb"(\d{1,3})%")
_SENT_RE = re.compile(r"sent ([\d,]+) bytes")


def _stream_rsync(origin: SshTarget, command: str, tarea_id: int, timeout: float = 86400.0) -> tuple[int, str]:
    """Ejecuta rsync en el origen y actualiza el porcentaje en BD según avanza."""
    with connect(origin, timeout=30) as client:
        transport = client.get_transport()
        chan = transport.open_session()
        chan.settimeout(timeout)
        chan.set_combine_stderr(True)
        chan.exec_command(command)

        tail = b""
        last_pct = -1
        buf = b""
        while True:
            if chan.recv_ready():
                data = chan.recv(8192)
                if not data:
                    break
                buf += data
                tail = (tail + data)[-8192:]
                # progress2 usa \r para refrescar la misma línea.
                chunks = re.split(rb"[\r\n]", buf)
                buf = chunks.pop()  # resto incompleto
                for chunk in chunks:
                    m = _PCT_RE.search(chunk)
                    if m:
                        pct = min(100, int(m.group(1)))
                        if pct != last_pct:
                            last_pct = pct
                            _update_pct(tarea_id, pct)
            elif chan.exit_status_ready():
                break
        rc = chan.recv_exit_status()
        # Drena lo que quede.
        while chan.recv_ready():
            tail = (tail + chan.recv(8192))[-8192:]
    return rc, tail.decode("utf-8", "replace")


def _update_pct(tarea_id: int, pct: int) -> None:
    with session_scope() as session:
        t = session.get(Tarea, tarea_id)
        if t:
            t.porcentaje = pct


def run_tarea(tarea_id: int, box: SecretBox) -> None:
    """Ejecuta una tarea completa de principio a fin, registrando el resultado."""
    # 1) Cargar datos y marcar en progreso + crear registro de ejecución.
    with session_scope() as session:
        tarea = session.get(Tarea, tarea_id)
        if tarea is None:
            return
        origen = session.get(Origen, tarea.origen_id)
        volumen = origen.volumen
        host = volumen.host_origen
        destino = session.get(Destino, tarea.destino_id)
        tarea.estado = "en_progreso"
        tarea.porcentaje = 0
        tarea.run_now = False
        ejec = Ejecucion(tarea_id=tarea_id, inicio=dt.datetime.now())
        session.add(ejec)
        session.flush()
        ejec_id = ejec.id
        # El conector define qué ruta copiar y con qué filtros (p. ej. bundle @).
        conector = get_connector(host.tipo_conector)
        ruta_origen, filtros = conector.fuente_rsync(origen.tipo, origen.ruta)
        # Copias defensivas de los datos necesarios.
        info = {
            "tipo": tarea.tipo,
            "ruta_origen": ruta_origen,
            "filtros": filtros,
            "carpeta_base": destino.carpeta_base,
            "host_nombre": host.nombre,
            "volumen_nombre": volumen.nombre,
            "origen_nombre": origen.nombre,
            "destino_usuario": destino.usuario,
            "destino_host": destino.host,
            "destino_puerto": destino.puerto,
            "retencion_dias": tarea.retencion_dias,
            "rsync_extra": tarea.rsync_extra,
            "comando_override": tarea.comando_rsync,
            "cron": tarea.cron,
        }
        origin_target = ssh_target_for_host(host, box)
        destino_target = ssh_target_for_destino(destino, box)

    resultado = "ok"
    error_msg = None
    sent_bytes = None
    snapshot_path = None
    try:
        # 2) Garantizar confianza SSH origen→destino (auto-provisión).
        key_path = ensure_trust(tarea_id, box)

        # 3) Construir el plan de esta ejecución.
        plan = build_plan(
            ruta_origen=info["ruta_origen"],
            carpeta_base=info["carpeta_base"],
            host_nombre=info["host_nombre"],
            volumen_nombre=info["volumen_nombre"],
            origen_nombre=info["origen_nombre"],
            tipo=info["tipo"],
            destino_usuario=info["destino_usuario"],
            destino_host=info["destino_host"],
            destino_puerto=info["destino_puerto"],
            key_path=key_path,
            extra_flags=info["rsync_extra"],
            filtros=info["filtros"],
        )
        snapshot_path = plan.dest_target

        # 4) Preparar carpetas en el destino (la raíz de la tarea debe existir).
        _prepare_destino(destino_target, plan.dest_root)

        # 5) Ejecutar rsync (override del usuario si lo definió).
        command = info["comando_override"] or plan.command
        rc, output = _stream_rsync(origin_target, command, tarea_id)

        m = _SENT_RE.search(output)
        if m:
            sent_bytes = int(m.group(1).replace(",", ""))

        # rsync rc 24 = "ficheros desaparecieron durante la copia": no es fatal.
        if rc not in (0, 24):
            resultado = "fallo"
            error_msg = output[-2000:]
        else:
            # 6) Post-proceso para incremental: enlazar current y aplicar retención.
            if info["tipo"] == "incremental" and plan.snapshot_name:
                set_current_symlink(destino_target, plan.dest_root, plan.snapshot_name)
                apply_retention(destino_target, plan.dest_root, info["retencion_dias"])
    except SshError as exc:
        resultado = "fallo"
        error_msg = str(exc)
    except Exception as exc:  # noqa: BLE001
        resultado = "fallo"
        error_msg = f"Error inesperado: {exc}"

    # 7) Persistir resultado y recalcular próxima ejecución.
    _finalize(tarea_id, ejec_id, resultado, error_msg, sent_bytes, snapshot_path, info["cron"])

    if resultado == "fallo":
        notify_failure(info["host_nombre"], info["origen_nombre"], error_msg or "")


def _prepare_destino(destino: SshTarget, dest_root: str) -> None:
    with connect(destino) as client:
        rc, _, err = run(client, f"mkdir -p {shlex.quote(dest_root)}")
        if rc != 0:
            raise SshError(f"No se pudo preparar la carpeta destino: {err}")


def _finalize(tarea_id, ejec_id, resultado, error_msg, sent_bytes, snapshot_path, cron):
    from croniter import croniter

    now = dt.datetime.now()
    try:
        next_run = croniter(cron, now).get_next(dt.datetime)
    except Exception:  # noqa: BLE001
        next_run = None

    with session_scope() as session:
        t = session.get(Tarea, tarea_id)
        if t:
            t.estado = "terminada" if resultado == "ok" else "fallida"
            t.porcentaje = 100 if resultado == "ok" else t.porcentaje
            t.last_run_at = now
            t.next_run_at = next_run
        e = session.get(Ejecucion, ejec_id)
        if e:
            e.fin = now
            e.resultado = resultado
            e.bytes_transferidos = sent_bytes
            e.snapshot_path = snapshot_path
            e.error = error_msg
            e.resumen = "Copia completada." if resultado == "ok" else None
