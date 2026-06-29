"""Gestión de volúmenes de destino."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import get_secret_box, require_login
from app.models import PROTECCIONES, Destino, Ubicacion
from app.remote import SshTarget, test_connection
from app.templating import templates

router = APIRouter(prefix="/destinos")


def _ubicaciones(session):
    return list(session.scalars(select(Ubicacion).order_by(Ubicacion.nombre)))


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        destinos = list(session.scalars(select(Destino).order_by(Destino.nombre)))
        rows = []
        for d in destinos:
            origenes = sorted({t.host_origen.nombre for t in d.tareas})
            rows.append(
                {
                    "id": d.id,
                    "nombre": d.nombre,
                    "host": d.host,
                    "estado": d.estado,
                    "proteccion": d.proteccion,
                    "ubicacion": d.ubicacion.nombre if d.ubicacion else "—",
                    "espacio_total": d.espacio_total,
                    "espacio_backups": d.espacio_backups,
                    "espacio_libre": d.espacio_libre,
                    "carpeta_base": d.carpeta_base,
                    "n_tareas": len(d.tareas),
                    "origenes": origenes,
                }
            )
    return templates.TemplateResponse(
        "destinos.html", {"request": request, "active": "destinos", "destinos": rows}
    )


@router.get("/nuevo", response_class=HTMLResponse)
async def nuevo_form(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        ubicaciones = [{"id": u.id, "nombre": u.nombre} for u in _ubicaciones(session)]
    return templates.TemplateResponse(
        "destino_form.html",
        {
            "request": request,
            "active": "destinos",
            "ubicaciones": ubicaciones,
            "protecciones": PROTECCIONES,
            "error": None,
            "form": {},
        },
    )


@router.post("", response_class=HTMLResponse)
async def crear(
    request: Request,
    nombre: str = Form(...),
    host: str = Form(...),
    puerto: int = Form(22),
    usuario: str = Form(...),
    auth_method: str = Form("password"),
    secret: str = Form(""),
    carpeta_base: str = Form(...),
    proteccion: str = Form("single"),
    ubicacion_id: str = Form(""),
    _: int = Depends(require_login),
):
    box = get_secret_box()
    form = {
        "nombre": nombre, "host": host, "puerto": puerto, "usuario": usuario,
        "auth_method": auth_method, "carpeta_base": carpeta_base,
        "proteccion": proteccion, "ubicacion_id": ubicacion_id,
    }

    def fail(msg: str):
        with session_scope() as session:
            ubicaciones = [{"id": u.id, "nombre": u.nombre} for u in _ubicaciones(session)]
        return templates.TemplateResponse(
            "destino_form.html",
            {
                "request": request, "active": "destinos", "ubicaciones": ubicaciones,
                "protecciones": PROTECCIONES, "error": msg, "form": form,
            },
        )

    with session_scope() as session:
        if session.scalar(select(Destino).where(Destino.nombre == nombre.strip())):
            return fail("Ya existe un destino con ese nombre.")
        destino = Destino(
            nombre=nombre.strip(),
            host=host.strip(),
            puerto=puerto,
            usuario=usuario.strip(),
            auth_method=auth_method if auth_method in ("key", "password") else "password",
            secret_cifrado=box.encrypt(secret),
            carpeta_base=carpeta_base.strip(),
            proteccion=proteccion if proteccion in PROTECCIONES else "single",
            ubicacion_id=int(ubicacion_id) if ubicacion_id else None,
        )
        session.add(destino)
    return RedirectResponse("/destinos", status_code=303)


@router.post("/{destino_id}/test")
async def probar(destino_id: int, _: int = Depends(require_login)):
    box = get_secret_box()
    with session_scope() as session:
        d = session.get(Destino, destino_id)
        if not d:
            return JSONResponse({"ok": False, "message": "No existe"}, status_code=404)
        target = SshTarget(d.host, d.puerto, d.usuario, d.auth_method, box.decrypt(d.secret_cifrado))
    ok, message = test_connection(target)
    return JSONResponse({"ok": ok, "message": message})


@router.post("/{destino_id}/eliminar")
async def eliminar(destino_id: int, _: int = Depends(require_login)):
    with session_scope() as session:
        d = session.get(Destino, destino_id)
        if d:
            if d.tareas:
                # No borrar destinos en uso para no dejar tareas huérfanas.
                return RedirectResponse("/destinos?error=en_uso", status_code=303)
            session.delete(d)
    return RedirectResponse("/destinos", status_code=303)
