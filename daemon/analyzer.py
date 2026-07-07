"""Analizador periódico de orígenes.

Dos funciones, ejecutadas cada X horas (configurable en ajustes) o a demanda:
  1. Refresca el tamaño de cada origen (``du``) y lo registra en el histórico.
  2. Re-explora los hosts con su conector para detectar orígenes nuevos y
     desaparecidos; los desaparecidos con tareas quedan huérfanos y se avisa por email.

Nunca borra orígenes ni tareas: los desaparecidos solo se marcan.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.crypto import SecretBox
from app.db import session_scope
from app.models import HistoricoTamano, HostOrigen, Origen
from app.remote import SshError, connect, run
from app.services import explorar_host, ssh_target_for_host
from connectors import get_connector
from daemon.notify import notify_orphan


# --- Helpers puros (testeables sin SSH) --------------------------------------

def registrar_tamano(session, origen_id: int, tamano: int) -> None:
    """Actualiza la caché de tamaño del origen y añade una fila al histórico."""
    origen = session.get(Origen, origen_id)
    if origen is None:
        return
    origen.tamano_bytes = tamano
    origen.last_size_check = dt.datetime.now()
    session.add(HistoricoTamano(origen_id=origen_id, bytes=tamano))


def nuevos_huerfanos(host: HostOrigen, estados_antes: dict[int, str]) -> list[tuple[str, str]]:
    """(host, origen) de los orígenes recién desaparecidos que tenían tareas."""
    avisos = []
    for vol in host.volumenes:
        for o in vol.origenes:
            recien = o.estado == "desaparecido" and estados_antes.get(o.id) == "activo"
            if recien and len(o.tareas) > 0:
                avisos.append((host.nombre, o.nombre))
    return avisos


# --- Orquestación (SSH) ------------------------------------------------------

def reexplorar(box: SecretBox) -> None:
    """Re-explora cada host y avisa por email de los orígenes huérfanos nuevos."""
    with session_scope() as session:
        host_ids = [h.id for h in session.scalars(select(HostOrigen))]
    for hid in host_ids:
        avisos: list[tuple[str, str]] = []
        try:
            with session_scope() as session:
                h = session.get(HostOrigen, hid)
                if h is None:
                    continue
                antes = {o.id: o.estado for v in h.volumenes for o in v.origenes}
                explorar_host(session, h, box)          # SSH + persistir (marca desaparecidos)
                avisos = nuevos_huerfanos(h, antes)
        except SshError:
            continue  # host inaccesible: lo gestiona el monitor, no es tarea del analizador
        for host_nombre, origen_nombre in avisos:
            notify_orphan(host_nombre, origen_nombre)


def refrescar_tamanos(box: SecretBox) -> None:
    """Mide el tamaño de cada origen activo y lo registra (caché + histórico)."""
    with session_scope() as session:
        host_ids = [h.id for h in session.scalars(select(HostOrigen))]
    for hid in host_ids:
        try:
            with session_scope() as session:
                h = session.get(HostOrigen, hid)
                if h is None:
                    continue
                conector = get_connector(h.tipo_conector)
                target = ssh_target_for_host(h, box)
                pendientes = [(o.id, o.tipo, o.ruta)
                              for v in h.volumenes for o in v.origenes if o.estado == "activo"]
            medidas: dict[int, int] = {}
            with connect(target) as client:
                def ejecutar(cmd: str):
                    return run(client, cmd)

                for origen_id, tipo, ruta in pendientes:
                    tam = conector.medir_tamano(ejecutar, tipo, ruta)
                    if tam is not None:
                        medidas[origen_id] = tam
            if medidas:
                with session_scope() as session:
                    for origen_id, tam in medidas.items():
                        registrar_tamano(session, origen_id, tam)
        except SshError:
            continue


def run_analisis(box: SecretBox) -> None:
    """Ejecuta el análisis completo: re-exploración + refresco de tamaños."""
    reexplorar(box)
    refrescar_tamanos(box)
