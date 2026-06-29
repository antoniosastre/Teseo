"""Gestión de ubicaciones físicas (lista desplegable con alta inline)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import require_login
from app.models import Ubicacion

router = APIRouter(prefix="/ubicaciones")


@router.post("")
async def crear_ubicacion(
    request: Request,
    nombre: str = Form(...),
    _: int = Depends(require_login),
):
    nombre = nombre.strip()
    if not nombre:
        return JSONResponse({"error": "Nombre vacío"}, status_code=400)
    with session_scope() as session:
        existing = session.scalar(select(Ubicacion).where(Ubicacion.nombre == nombre))
        if existing:
            ubic_id = existing.id
        else:
            ubic = Ubicacion(nombre=nombre)
            session.add(ubic)
            session.flush()
            ubic_id = ubic.id
    # Respuesta JSON para el alta inline desde formularios (fetch).
    return JSONResponse({"id": ubic_id, "nombre": nombre})
