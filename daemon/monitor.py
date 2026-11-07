"""Monitorización periódica: espacio de destinos y accesibilidad de orígenes."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from app.crypto import SecretBox
from app.db import session_scope
from app.models import Destino, HostOrigen
from app.remote import SshError, backups_size, disk_space, test_connection
from app.services import ssh_target_for_destino, ssh_target_for_host
from daemon.notify import notify_unreachable

log = logging.getLogger("teseod")


def check_destinos(box: SecretBox) -> None:
    """Accesibilidad y espacio libre de cada destino (cada pocos minutos).

    Solo usa ``df``, que es instantáneo. Lo que ocupan las copias (``du``) se
    mide en el analizador (``medir_backups_destinos``): recorrer millones de
    ficheros cada 5 minutos competía con las copias y superaba el timeout del
    canal SSH, que dejaba el destino marcado como inaccesible sin estarlo.
    """
    with session_scope() as session:
        destinos = list(session.scalars(select(Destino)))
    for d in destinos:
        espacio = None
        try:
            target = _target_destino(d, box)
            espacio = disk_space(target, d.carpeta_base)
            estado = "conectado"
            if espacio is None:
                log.warning("Destino %s: no se pudo interpretar 'df -Pk'", d.nombre)
        except SshError:
            estado = "inaccesible"
        with session_scope() as session:
            obj = session.get(Destino, d.id)
            if obj:
                obj.estado = estado
                if espacio is not None:
                    obj.espacio_total, obj.espacio_libre = espacio
                obj.last_check = dt.datetime.now()


def medir_backups_destinos(box: SecretBox) -> None:
    """Mide lo que ocupan las copias en cada destino (``du``; puede tardar horas).

    Un fallo o un timeout no cambian el estado del destino (eso es cosa del
    monitor): simplemente se conserva la última medida.
    """
    with session_scope() as session:
        destinos = list(session.scalars(select(Destino)))
    for d in destinos:
        try:
            tam = backups_size(_target_destino(d, box), d.carpeta_base)
        except SshError as exc:
            log.warning("Destino %s: no se pudo medir el espacio de las copias: %s", d.nombre, exc)
            continue
        if tam is None:
            continue
        with session_scope() as session:
            obj = session.get(Destino, d.id)
            if obj:
                obj.espacio_backups = tam


def check_origenes(box: SecretBox) -> None:
    with session_scope() as session:
        hosts = list(session.scalars(select(HostOrigen)))
    for h in hosts:
        target = ssh_target_for_host(h, box)
        ok, _, learned = test_connection(target)
        nuevo = "conectado" if ok else "inaccesible"
        with session_scope() as session:
            obj = session.get(HostOrigen, h.id)
            if obj:
                previo = obj.estado_conexion
                obj.estado_conexion = nuevo
                obj.last_check = dt.datetime.now()
                # Primer uso: fija la clave de host aprendida (pinning).
                if learned and not obj.host_key:
                    obj.host_key = learned
        # Avisa solo en la transición a inaccesible (evita spam).
        if not ok and previo != "inaccesible":
            notify_unreachable(h.nombre)


def _target_destino(destino, box):
    return ssh_target_for_destino(destino, box)
