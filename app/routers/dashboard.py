"""Panel principal con resumen."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from app.db import session_scope
from app.deps import require_login
from app.models import Destino, Ejecucion, HostOrigen, Tarea
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        n_hosts = session.scalar(select(func.count()).select_from(HostOrigen))
        n_destinos = session.scalar(select(func.count()).select_from(Destino))
        n_tareas = session.scalar(select(func.count()).select_from(Tarea))
        en_progreso = session.scalar(
            select(func.count()).select_from(Tarea).where(Tarea.estado == "en_progreso")
        )
        fallidas = session.scalar(
            select(func.count()).select_from(Tarea).where(Tarea.estado == "fallida")
        )
        # Todas las tareas configuradas, con su estado (la web lo refresca por SSE).
        data_tareas = [
            {
                "id": t.id,
                "host": t.origen.volumen.host_origen.nombre,
                "origen": t.origen.nombre,
                "destino": t.destino.nombre,
                "tipo": t.tipo,
                "estado": t.estado,
                "porcentaje": t.porcentaje,
                "activa": t.activa,
                "last_run_at": t.last_run_at,
                "next_run_at": t.next_run_at,
            }
            for t in session.scalars(select(Tarea).order_by(Tarea.id))
        ]
        ultimas = list(
            session.scalars(select(Ejecucion).order_by(Ejecucion.inicio.desc()).limit(10))
        )
        # Precargar relaciones para el template.
        data_ultimas = [
            {
                "tarea": e.tarea.origen.nombre,
                "host": e.tarea.origen.volumen.host_origen.nombre,
                "inicio": e.inicio,
                "fin": e.fin,
                "resultado": e.resultado,
            }
            for e in ultimas
        ]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "active": "dashboard",
            "n_hosts": n_hosts,
            "n_destinos": n_destinos,
            "n_tareas": n_tareas,
            "en_progreso": en_progreso,
            "fallidas": fallidas,
            "tareas": data_tareas,
            "ultimas": data_ultimas,
        },
    )
