"""Funciones de apoyo compartidas entre routers y daemon."""
from __future__ import annotations

import datetime as dt
import json

from app.crypto import SecretBox
from app.models import Destino, HostOrigen, Origen, Tarea, Volumen
from app.remote import SshTarget, connect, run
from connectors import VolumenDescubierto, get_connector
from scoring import CopiaInputs, ScoreBar, origen_score, score_bar


def ssh_target_for_host(host: HostOrigen, box: SecretBox) -> SshTarget:
    return SshTarget(
        host=host.host,
        port=host.puerto,
        usuario=host.usuario,
        auth_method=host.auth_method,
        secret=box.decrypt(host.secret_cifrado),
        host_key=host.host_key,
    )


def ssh_target_for_destino(destino: Destino, box: SecretBox) -> SshTarget:
    return SshTarget(
        host=destino.host,
        port=destino.puerto,
        usuario=destino.usuario,
        auth_method=destino.auth_method,
        secret=box.decrypt(destino.secret_cifrado),
        host_key=destino.host_key,
    )


def host_de_origen(origen: Origen) -> HostOrigen:
    return origen.volumen.host_origen


def _tareas_de_host(host: HostOrigen) -> list[Tarea]:
    return [t for vol in host.volumenes for o in vol.origenes for t in o.tareas]


def host_semaforo(host: HostOrigen) -> str:
    """Color del semáforo de un host según el estado de sus tareas.

    rojo    -> origen inaccesible
    naranja -> alguna tarea fallida
    amarillo-> alguna tarea en progreso
    verde   -> todo correcto
    gris    -> sin tareas / sin datos
    """
    if host.estado_conexion == "inaccesible":
        return "rojo"
    estados = [t.estado for t in _tareas_de_host(host) if t.activa]
    if not estados:
        return "gris"
    if "fallida" in estados:
        return "naranja"
    if "en_progreso" in estados:
        return "amarillo"
    if all(e in ("terminada", "esperando") for e in estados):
        return "verde"
    return "gris"


def _ubicacion_distinta(host: HostOrigen, destino: Destino) -> bool:
    return (
        host.ubicacion_id is not None
        and destino.ubicacion_id is not None
        and host.ubicacion_id != destino.ubicacion_id
    )


def origen_score_bar(origen: Origen) -> ScoreBar:
    """Barra de protección de un ORIGEN (regla "mejor copia").

    RAID del volumen + (si tiene copias) +1 y la mejor de sus tareas por el lado
    destino (RAID destino + ubicación distinta).
    """
    host = host_de_origen(origen)
    copias = [
        CopiaInputs(
            destino_proteccion=t.destino.proteccion,
            ubicacion_distinta=_ubicacion_distinta(host, t.destino),
        )
        for t in origen.tareas
        if t.activa
    ]
    return score_bar(origen_score(origen.volumen.proteccion, copias))


def estado_copia(origen: Origen) -> str:
    """Estado de copia agregado de un origen a partir de sus tareas.

    Precedencia: sin_copia < pendiente < correcta, con en_proceso y error
    teniendo prioridad para hacerse notar.
    """
    activas = [t for t in origen.tareas if t.activa]
    if not activas:
        return "sin_copia"
    estados = {t.estado for t in activas}
    if "en_progreso" in estados:
        return "en_proceso"
    if "fallida" in estados:
        return "error"
    if "terminada" in estados:
        return "correcta"
    return "pendiente"  # tareas creadas pero aún sin ejecutar


def persistir_descubrimiento(session, host: HostOrigen, volumenes: list[VolumenDescubierto]) -> None:
    """Vuelca el descubrimiento del conector en BD (crea/actualiza; nunca borra).

    Reutilizable por el alta inicial y por el analizador (re-exploración): los
    orígenes que ya no aparezcan se marcan como "desaparecido" (tareas huérfanas),
    pero jamás se eliminan automáticamente.
    """
    vol_por_nombre = {v.nombre: v for v in host.volumenes}
    vistos: set = set()
    for vd in volumenes:
        vol = vol_por_nombre.get(vd.nombre)
        if vol is None:
            vol = Volumen(nombre=vd.nombre, dispositivo=vd.dispositivo)
            host.volumenes.append(vol)   # mantiene la colección en memoria coherente
            vol_por_nombre[vd.nombre] = vol
        elif vd.dispositivo:
            vol.dispositivo = vd.dispositivo
        org_por_ruta = {o.ruta: o for o in vol.origenes}
        for od in vd.origenes:
            org = org_por_ruta.get(od.ruta)
            if org is None:
                org = Origen(nombre=od.nombre, tipo=od.tipo, ruta=od.ruta)
                vol.origenes.append(org)
            else:
                org.estado = "activo"   # reaparecido
                org.nombre = od.nombre
            vistos.add(org)
    # Orígenes que existían y ya no se descubren -> desaparecidos (huérfanos, no se borran).
    for vol in host.volumenes:
        for o in vol.origenes:
            if o not in vistos:
                o.estado = "desaparecido"
    session.flush()


def opciones_conector(host: HostOrigen) -> dict:
    """Opciones de descubrimiento persistidas en el host (JSON) como dict."""
    if not host.conector_opciones:
        return {}
    try:
        return json.loads(host.conector_opciones)
    except (ValueError, TypeError):
        return {}


def explorar_host(session, host: HostOrigen, box: SecretBox) -> None:
    """Conecta al host, ejecuta el conector y persiste los orígenes descubiertos."""
    conector = get_connector(host.tipo_conector)
    opciones = opciones_conector(host)
    target = ssh_target_for_host(host, box)
    with connect(target) as client:
        def ejecutar(cmd: str):
            return run(client, cmd)

        volumenes = conector.descubrir(ejecutar, opciones)
    persistir_descubrimiento(session, host, volumenes)


def evolucion_tamano(origen: Origen, limite: int = 30) -> tuple[list[dict], dict | None]:
    """Serie reciente de tamaños del origen + resumen de crecimiento.

    Devuelve (muestras, resumen). ``muestras`` van de más reciente a más antigua,
    cada una con su delta respecto a la anterior medición. ``resumen`` es None si
    aún no hay al menos dos mediciones para comparar.
    """
    hist = list(origen.historicos)[:limite]  # relación ya ordenada desc por timestamp
    muestras = []
    for i, h in enumerate(hist):
        anterior = hist[i + 1].bytes if i + 1 < len(hist) else None
        muestras.append({
            "timestamp": h.timestamp,
            "bytes": h.bytes,
            "delta": (h.bytes - anterior) if anterior is not None else None,
        })
    resumen = None
    if len(hist) >= 2:
        resumen = {
            "actual": hist[0].bytes,
            "delta_anterior": hist[0].bytes - hist[1].bytes,
            "delta_ventana": hist[0].bytes - hist[-1].bytes,
            "muestras": len(hist),
            "desde": hist[-1].timestamp,
        }
    return muestras, resumen


def ultima_copia_ok(origen: Origen) -> dt.datetime | None:
    """Timestamp de la última ejecución correcta entre todas las tareas del origen."""
    fines = [
        e.fin
        for t in origen.tareas
        for e in t.ejecuciones
        if e.resultado == "ok" and e.fin is not None
    ]
    return max(fines) if fines else None
