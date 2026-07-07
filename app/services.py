"""Funciones de apoyo compartidas entre routers y daemon."""
from __future__ import annotations

from app.crypto import SecretBox
from app.models import Destino, HostOrigen, Tarea
from app.remote import SshTarget
from scoring import ScoreInputs, classify, score


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


def host_semaforo(host: HostOrigen) -> str:
    """Color del semáforo de un host según el estado de sus tareas.

    rojo   -> origen inaccesible
    naranja-> alguna tarea fallida
    amarillo-> alguna tarea en progreso
    verde  -> todo correcto
    gris   -> sin tareas / sin datos
    """
    if host.estado_conexion == "inaccesible":
        return "rojo"
    estados = [t.estado for t in host.tareas if t.activa]
    if not estados:
        return "gris"
    if "fallida" in estados:
        return "naranja"
    if "en_progreso" in estados:
        return "amarillo"
    if all(e in ("terminada", "esperando") for e in estados):
        return "verde"
    return "gris"


def tarea_score(tarea: Tarea) -> tuple[int, str]:
    """Devuelve (puntos, clasificación) de la protección de una tarea."""
    host = tarea.host_origen
    destino = tarea.destino
    pts = score(
        ScoreInputs(
            origen_proteccion=host.es_raid,
            destino_proteccion=destino.proteccion,
            origen_ubicacion_id=host.ubicacion_id,
            destino_ubicacion_id=destino.ubicacion_id,
        )
    )
    return pts, classify(pts)
