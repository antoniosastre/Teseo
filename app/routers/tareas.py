"""Acciones sobre tareas: ejecutar ya, activar/desactivar, eliminar, historial."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import require_login
from app.models import Ejecucion, Tarea
from app.services import tarea_score
from app.templating import templates

router = APIRouter(prefix="/tareas")


@router.post("/{tarea_id}/run")
async def run_now(tarea_id: int, _: int = Depends(require_login)):
    with session_scope() as session:
        t = session.get(Tarea, tarea_id)
        if t and t.estado != "en_progreso":
            t.run_now = True
            t.estado = "esperando"
    return RedirectResponse("/origenes", status_code=303)


@router.post("/{tarea_id}/toggle")
async def toggle(tarea_id: int, _: int = Depends(require_login)):
    with session_scope() as session:
        t = session.get(Tarea, tarea_id)
        if t:
            t.activa = not t.activa
    return RedirectResponse("/origenes", status_code=303)


@router.post("/{tarea_id}/eliminar")
async def eliminar(tarea_id: int, _: int = Depends(require_login)):
    with session_scope() as session:
        t = session.get(Tarea, tarea_id)
        if t:
            session.delete(t)
    return RedirectResponse("/origenes", status_code=303)


@router.get("/{tarea_id}", response_class=HTMLResponse)
async def detalle(tarea_id: int, request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        t = session.get(Tarea, tarea_id)
        if not t:
            return RedirectResponse("/origenes", status_code=303)
        sb = tarea_score(t)
        ejecuciones = [
            {
                "inicio": e.inicio, "fin": e.fin, "resultado": e.resultado,
                "bytes": e.bytes_transferidos, "snapshot": e.snapshot_path,
                "resumen": e.resumen, "error": e.error,
            }
            for e in session.scalars(
                select(Ejecucion).where(Ejecucion.tarea_id == tarea_id).order_by(Ejecucion.inicio.desc()).limit(50)
            )
        ]
        data = {
            "id": t.id,
            "carpeta_origen": t.carpeta_origen,
            "host": t.host_origen.nombre,
            "destino": t.destino.nombre,
            "tipo": t.tipo,
            "cron": t.cron,
            "estado": t.estado,
            "porcentaje": t.porcentaje,
            "retencion": t.retencion,
            "comando_rsync": t.comando_rsync,
            "rsync_extra": t.rsync_extra,
            "last_run_at": t.last_run_at,
            "next_run_at": t.next_run_at,
            "score_pct": sb.pct,
            "score_color": sb.color,
            "score_texto": sb.texto,
        }
    return templates.TemplateResponse(
        "tarea_detalle.html",
        {"request": request, "active": "origenes", "tarea": data, "ejecuciones": ejecuciones},
    )
