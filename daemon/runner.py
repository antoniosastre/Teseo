"""Ejecución de tareas de backup: rsync DESACOPLADO del controlador.

El controlador abre SSH al host ORIGEN solo para dos cosas breves:
  1. lanzar la copia como proceso descolgado (``setsid``) que escribe su estado en
     ``~/.teseo/`` (log, pid y, al terminar, el código de salida en ``.rc``);
  2. sondear ese estado en cada tick del daemon.

Así la copia **sobrevive a reinicios/caídas del controlador** (no es hija del canal
SSH), el canal no se mantiene abierto durante horas, y el rsync corre en una shell
normal del origen (el contexto que funciona), no como hijo directo de un exec de paramiko.

La preparación de carpetas del destino, el enlace ``current`` y la retención se hacen
por SSH al DESTINO desde el controlador (igual que antes).
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select

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

_PCT_RE = re.compile(r"(\d{1,3})%")
_SENT_RE = re.compile(r"sent ([\d,]+) bytes")
_TESEO_DIR = "~/.teseo"
_GRACIA_ARRANQUE_S = 45  # margen tras lanzar antes de considerar un pid ausente como "interrumpida"


# --- Rutas y script en el origen --------------------------------------------

def _rutas(tarea_id: int) -> dict:
    base = f"{_TESEO_DIR}/task_{tarea_id}"
    return {"sh": f"{base}.sh", "log": f"{base}.log", "rc": f"{base}.rc", "pid": f"{base}.pid"}


def _script_copia(command: str, tarea_id: int) -> str:
    """Script POSIX que registra su PID, ejecuta la copia y escribe su código de salida.

    La existencia del fichero ``.rc`` marca "copia finalizada"; el ``.pid`` permite
    saber si sigue viva (``kill -0``).
    """
    r = _rutas(tarea_id)
    return (
        "#!/bin/sh\n"
        f"mkdir -p {_TESEO_DIR}\n"
        f"rm -f {r['rc']}\n"
        f"echo $$ > {r['pid']}\n"
        f"{command} > {r['log']} 2>&1\n"
        f"echo $? > {r['rc']}\n"
    )


def _lanzar(origin: SshTarget, tarea_id: int, command: str) -> None:
    """Escribe el script en el origen y lo lanza descolgado (setsid)."""
    r = _rutas(tarea_id)
    script = _script_copia(command, tarea_id)
    with connect(origin) as client:
        run(client, f"mkdir -p {_TESEO_DIR}")
        rc, _, err = run(client, f"cat > {r['sh']} <<'TESEO_EOF'\n{script}\nTESEO_EOF")
        if rc != 0:
            raise SshError(f"No se pudo escribir el script de copia en el origen: {err}")
        # setsid + redirección desprende el proceso del canal SSH: sobrevive al cierre.
        rc, _, err = run(client, f"setsid sh {r['sh']} >/dev/null 2>&1 &")
        if rc != 0:
            raise SshError(f"No se pudo lanzar la copia en el origen: {err}")


def _parse_estado(out: str) -> tuple[int | None, bool, str]:
    """Parsea la salida del sondeo -> (rc | None si sigue, pid_vivo, cola_del_log)."""
    marker = "===LOG==="
    head, sep, log = out.partition(marker + "\n")
    if not sep:
        head, log = out, ""
    rc: int | None = None
    alive = False
    for line in head.splitlines():
        if line.startswith("RC=") and line[3:].strip():
            try:
                rc = int(line[3:].strip())
            except ValueError:
                rc = None
        elif line.startswith("ALIVE="):
            alive = line[6:].strip() == "yes"
    return rc, alive, log


def _estado_copia(origin: SshTarget, tarea_id: int) -> tuple[int | None, bool, str]:
    """Lee por SSH el estado de la copia descolgada en el origen."""
    r = _rutas(tarea_id)
    cmd = (
        f'rc=$(cat {r["rc"]} 2>/dev/null); '
        f'a=no; kill -0 "$(cat {r["pid"]} 2>/dev/null)" 2>/dev/null && a=yes; '
        f'printf "RC=%s\\nALIVE=%s\\n===LOG===\\n" "$rc" "$a"; '
        f'tail -c 8192 {r["log"]} 2>/dev/null'
    )
    with connect(origin, timeout=30) as client:
        _, out, _ = run(client, cmd, timeout=60)
    return _parse_estado(out)


def _limpiar(origin: SshTarget, tarea_id: int) -> None:
    """Borra los ficheros de estado de la copia (best-effort)."""
    r = _rutas(tarea_id)
    try:
        with connect(origin, timeout=30) as client:
            run(client, f"rm -f {r['sh']} {r['log']} {r['rc']} {r['pid']}")
    except SshError:
        pass


# --- Lanzamiento -------------------------------------------------------------

def lanzar_tarea(tarea_id: int, box: SecretBox) -> None:
    """Prepara y LANZA la copia descolgada. No espera a que termine (eso lo hace el sondeo)."""
    with session_scope() as session:
        tarea = session.get(Tarea, tarea_id)
        if tarea is None:
            return
        origen = session.get(Origen, tarea.origen_id)
        volumen = origen.volumen
        host = volumen.host_origen
        destino = session.get(Destino, tarea.destino_id)
        conector = get_connector(host.tipo_conector)
        ruta_origen, filtros = conector.fuente_rsync(origen.tipo, origen.ruta)
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
            "rsync_extra": tarea.rsync_extra,
            "comando_override": tarea.comando_rsync,
            "cron": tarea.cron,
        }
        origin_target = ssh_target_for_host(host, box)
        destino_target = ssh_target_for_destino(destino, box)
        # Marcar en progreso + abrir registro de ejecución.
        tarea.estado = "en_progreso"
        tarea.porcentaje = 0
        tarea.run_now = False
        ejec = Ejecucion(tarea_id=tarea_id, inicio=dt.datetime.now())
        session.add(ejec)
        session.flush()
        ejec_id = ejec.id

    try:
        key_path = ensure_trust(tarea_id, box)
        plan = build_plan(
            ruta_origen=info["ruta_origen"], carpeta_base=info["carpeta_base"],
            host_nombre=info["host_nombre"], volumen_nombre=info["volumen_nombre"],
            origen_nombre=info["origen_nombre"], tipo=info["tipo"],
            destino_usuario=info["destino_usuario"], destino_host=info["destino_host"],
            destino_puerto=info["destino_puerto"], key_path=key_path,
            extra_flags=info["rsync_extra"], filtros=info["filtros"],
        )
        _prepare_destino(destino_target, plan.dest_root)
        command = info["comando_override"] or plan.command
        _lanzar(origin_target, tarea_id, command)
        # Guardar el destino de esta ejecución para el post-proceso al finalizar.
        with session_scope() as session:
            e = session.get(Ejecucion, ejec_id)
            if e:
                e.snapshot_path = plan.dest_target
    except SshError as exc:
        _finalize(tarea_id, ejec_id, "fallo", str(exc), None, None, info["cron"])
        notify_failure(info["host_nombre"], info["origen_nombre"], str(exc))
    except Exception as exc:  # noqa: BLE001
        _finalize(tarea_id, ejec_id, "fallo", f"Error inesperado: {exc}", None, None, info["cron"])
        notify_failure(info["host_nombre"], info["origen_nombre"], f"Error inesperado: {exc}")


# --- Sondeo ------------------------------------------------------------------

def sondear_tarea(tarea_id: int, box: SecretBox) -> str:
    """Consulta el estado de una copia en curso y la finaliza si ha terminado.

    Devuelve: "running" | "starting" | "done" | "interrupted" | "unreachable" | "n/a".
    """
    with session_scope() as session:
        tarea = session.get(Tarea, tarea_id)
        if tarea is None or tarea.estado != "en_progreso":
            return "n/a"
        origen = session.get(Origen, tarea.origen_id)
        host = origen.volumen.host_origen
        destino = session.get(Destino, tarea.destino_id)
        info = {
            "tipo": tarea.tipo, "retencion_dias": tarea.retencion_dias, "cron": tarea.cron,
            "host_nombre": host.nombre, "origen_nombre": origen.nombre,
        }
        origin_target = ssh_target_for_host(host, box)
        destino_target = ssh_target_for_destino(destino, box)
        ejec = session.scalars(
            select(Ejecucion).where(Ejecucion.tarea_id == tarea_id, Ejecucion.fin.is_(None))
            .order_by(Ejecucion.id.desc())
        ).first()
        ejec_id = ejec.id if ejec else None
        inicio = ejec.inicio if ejec else None
        snapshot_path = ejec.snapshot_path if ejec else None

    try:
        rc, alive, log = _estado_copia(origin_target, tarea_id)
    except SshError:
        return "unreachable"  # origen inaccesible ahora; se reintenta en el próximo tick

    # Aún sin código de salida: sigue corriendo (o recién lanzada / interrumpida).
    if rc is None:
        if alive:
            pcts = _PCT_RE.findall(log)
            if pcts:
                _update_pct(tarea_id, min(100, int(pcts[-1])))
            return "running"
        # pid ausente y sin .rc: si acaba de lanzarse, dale margen; si no, interrumpida.
        if inicio and (dt.datetime.now() - inicio).total_seconds() < _GRACIA_ARRANQUE_S:
            return "starting"
        _finalize(tarea_id, ejec_id, "fallo",
                  "Copia interrumpida: el proceso no está en el origen y no dejó código de salida.",
                  None, snapshot_path, info["cron"])
        notify_failure(info["host_nombre"], info["origen_nombre"], "Copia interrumpida.")
        _limpiar(origin_target, tarea_id)
        return "interrupted"

    # Terminada: hay código de salida.
    sent_bytes = None
    m = _SENT_RE.search(log)
    if m:
        sent_bytes = int(m.group(1).replace(",", ""))

    resultado = "ok"
    error_msg = None
    # rsync rc 24 = "ficheros desaparecieron durante la copia": no es fatal.
    if rc in (0, 24):
        if info["tipo"] == "incremental" and snapshot_path:
            base = snapshot_path.rstrip("/")
            dest_root, _, snapshot_name = base.rpartition("/")
            try:
                set_current_symlink(destino_target, dest_root, snapshot_name)
                apply_retention(destino_target, dest_root, info["retencion_dias"])
            except SshError as exc:
                resultado, error_msg = "fallo", f"Copia OK pero falló el post-proceso: {exc}"
    else:
        resultado, error_msg = "fallo", log[-2000:]

    _finalize(tarea_id, ejec_id, resultado, error_msg, sent_bytes, snapshot_path, info["cron"])
    _limpiar(origin_target, tarea_id)
    if resultado == "fallo":
        notify_failure(info["host_nombre"], info["origen_nombre"], error_msg or "")
    return "done"


# --- Auxiliares --------------------------------------------------------------

def _update_pct(tarea_id: int, pct: int) -> None:
    with session_scope() as session:
        t = session.get(Tarea, tarea_id)
        if t:
            t.porcentaje = pct


def _prepare_destino(destino: SshTarget, dest_root: str) -> None:
    import shlex
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
        e = session.get(Ejecucion, ejec_id) if ejec_id else None
        if e:
            e.fin = now
            e.resultado = resultado
            e.bytes_transferidos = sent_bytes
            if snapshot_path:
                e.snapshot_path = snapshot_path
            e.error = error_msg
            e.resumen = "Copia completada." if resultado == "ok" else None
