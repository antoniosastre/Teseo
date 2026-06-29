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
        ultimas = list(
            session.scalars(select(Ejecucion).order_by(Ejecucion.inicio.desc()).limit(10))
        )
        # Precargar relaciones para el template.
        data_ultimas = [
            {
                "tarea": e.tarea.carpeta_origen,
                "host": e.tarea.host_origen.nombre,
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
            "ultimas": data_ultimas,
        },
    )
