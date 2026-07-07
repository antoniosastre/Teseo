"""Sección de Ajustes: analizador, ubicaciones físicas."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import require_login
from app.models import Destino, HostOrigen, Ubicacion
from app.settings import (
    ANALIZADOR_INTERVALO_HORAS,
    intervalo_analizador_horas,
    marcar_analizador_run_now,
    set_ajuste,
)
from app.templating import templates

router = APIRouter(prefix="/ajustes")


@router.get("", response_class=HTMLResponse)
async def ver(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        intervalo = intervalo_analizador_horas(session)
        ubicaciones = [
            {"id": u.id, "nombre": u.nombre, "en_uso": _en_uso(session, u.id)}
            for u in session.scalars(select(Ubicacion).order_by(Ubicacion.nombre))
        ]
    return templates.TemplateResponse(
        "ajustes.html",
        {"request": request, "active": "ajustes", "intervalo": intervalo,
         "ubicaciones": ubicaciones, "aviso": request.query_params.get("aviso")},
    )


@router.post("/analizador")
async def guardar_analizador(intervalo_horas: int = Form(...), _: int = Depends(require_login)):
    with session_scope() as session:
        set_ajuste(session, ANALIZADOR_INTERVALO_HORAS, str(max(1, intervalo_horas)))
    return RedirectResponse("/ajustes?aviso=intervalo-guardado", status_code=303)


@router.post("/analizar")
async def analizar_ahora(_: int = Depends(require_login)):
    """Marca la bandera que el daemon recoge para lanzar el análisis ya."""
    with session_scope() as session:
        marcar_analizador_run_now(session, True)
    return RedirectResponse("/ajustes?aviso=analisis-encolado", status_code=303)


@router.post("/ubicaciones")
async def crear_ubicacion(nombre: str = Form(...), _: int = Depends(require_login)):
    nombre = nombre.strip()
    if nombre:
        with session_scope() as session:
            if not session.scalar(select(Ubicacion).where(Ubicacion.nombre == nombre)):
                session.add(Ubicacion(nombre=nombre))
    return RedirectResponse("/ajustes", status_code=303)


@router.post("/ubicaciones/{ubic_id}/eliminar")
async def eliminar_ubicacion(ubic_id: int, _: int = Depends(require_login)):
    with session_scope() as session:
        if _en_uso(session, ubic_id):
            return RedirectResponse("/ajustes?aviso=ubicacion-en-uso", status_code=303)
        u = session.get(Ubicacion, ubic_id)
        if u:
            session.delete(u)
    return RedirectResponse("/ajustes", status_code=303)


def _en_uso(session, ubic_id: int) -> bool:
    hay_host = session.scalar(select(HostOrigen.id).where(HostOrigen.ubicacion_id == ubic_id))
    hay_dest = session.scalar(select(Destino.id).where(Destino.ubicacion_id == ubic_id))
    return bool(hay_host or hay_dest)
