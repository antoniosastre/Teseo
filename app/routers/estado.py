"""Estado en vivo: SSE de progreso y snapshot JSON (fallback polling)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import require_login
from app.models import HostOrigen, Tarea
from app.services import host_semaforo


def _snapshot() -> dict:
    with session_scope() as session:
        tareas = {
            t.id: {"estado": t.estado, "porcentaje": t.porcentaje,
                   "velocidad": t.velocidad, "cancelando": t.cancel_requested}
            for t in session.scalars(select(Tarea))
        }
        hosts = {
            h.id: {"semaforo": host_semaforo(h), "estado_conexion": h.estado_conexion}
            for h in session.scalars(select(HostOrigen))
        }
    return {"tareas": tareas, "hosts": hosts}


router = APIRouter(prefix="/estado")


@router.get("/json")
async def estado_json(_: int = Depends(require_login)):
    # to_thread: la consulta a BD es síncrona; fuera del event loop para no
    # congelar el resto de peticiones (incluidos los streams SSE) si BD se atasca.
    return JSONResponse(await asyncio.to_thread(_snapshot))


@router.get("/stream")
async def estado_stream(request: Request, _: int = Depends(require_login)):
    """Server-Sent Events: emite el estado cada 2s mientras el cliente escucha."""

    async def event_gen():
        while True:
            if await request.is_disconnected():
                break
            payload = json.dumps(await asyncio.to_thread(_snapshot))
            yield f"data: {payload}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
