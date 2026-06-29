"""Monitorización periódica: espacio de destinos y accesibilidad de orígenes."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.crypto import SecretBox
from app.db import session_scope
from app.models import Destino, HostOrigen
from app.remote import SshError, disk_usage, test_connection
from app.services import ssh_target_for_destino, ssh_target_for_host
from daemon.notify import notify_unreachable


def check_destinos(box: SecretBox) -> None:
    with session_scope() as session:
        destinos = list(session.scalars(select(Destino)))
    for d in destinos:
        try:
            target = _target_destino(d, box)
            usage = disk_usage(target, d.carpeta_base)
            estado, total, libre, backups = "conectado", usage.total, usage.free, usage.backups
        except SshError:
            estado, total, libre, backups = "inaccesible", None, None, None
        with session_scope() as session:
            obj = session.get(Destino, d.id)
            if obj:
                obj.estado = estado
                if total is not None:
                    obj.espacio_total = total
                    obj.espacio_libre = libre
                    obj.espacio_backups = backups
                obj.last_check = dt.datetime.now()


def check_origenes(box: SecretBox) -> None:
    with session_scope() as session:
        hosts = list(session.scalars(select(HostOrigen)))
    for h in hosts:
        target = ssh_target_for_host(h, box)
        ok, _ = test_connection(target)
        nuevo = "conectado" if ok else "inaccesible"
        with session_scope() as session:
            obj = session.get(HostOrigen, h.id)
            if obj:
                previo = obj.estado_conexion
                obj.estado_conexion = nuevo
                obj.last_check = dt.datetime.now()
        # Avisa solo en la transición a inaccesible (evita spam).
        if not ok and previo != "inaccesible":
            notify_unreachable(h.nombre)


def _target_destino(destino, box):
    return ssh_target_for_destino(destino, box)
