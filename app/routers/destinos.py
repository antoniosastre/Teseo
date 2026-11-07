"""Gestión de volúmenes de destino."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import get_secret_box, require_login
from app.models import PROTECCIONES, Destino, Ubicacion
from app.remote import SshTarget, test_connection
from app.rsync_cmd import validate_rsync_remoto
from app.templating import templates

router = APIRouter(prefix="/destinos")

# Tope razonable de copias simultáneas por destino (la cola global ya limita a
# daemon.teseod.MAX_CONCURRENCY; esto solo evita valores absurdos).
MAX_COPIAS_DESTINO = 16


def _ubicaciones(session):
    return list(session.scalars(select(Ubicacion).order_by(Ubicacion.nombre)))


def _opciones_copia(max_copias: int, rsync_remoto: str) -> tuple[int, str | None, str | None]:
    """Normaliza y valida las opciones de copia. Devuelve (max_copias, rsync_remoto, error)."""
    max_copias = min(MAX_COPIAS_DESTINO, max(1, max_copias))
    rsync_remoto = rsync_remoto.strip()
    error = validate_rsync_remoto(rsync_remoto)
    return max_copias, (rsync_remoto or None), error


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        destinos = list(session.scalars(select(Destino).order_by(Destino.nombre)))
        rows = []
        for d in destinos:
            origenes = sorted({t.origen.volumen.host_origen.nombre for t in d.tareas})
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
                    "max_copias": d.max_copias,
                    "rsync_remoto": d.rsync_remoto,
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
    max_copias: int = Form(1),
    rsync_remoto: str = Form(""),
    _: int = Depends(require_login),
):
    box = get_secret_box()
    form = {
        "nombre": nombre, "host": host, "puerto": puerto, "usuario": usuario,
        "auth_method": auth_method, "carpeta_base": carpeta_base,
        "proteccion": proteccion, "ubicacion_id": ubicacion_id,
        "max_copias": max_copias, "rsync_remoto": rsync_remoto,
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

    max_copias, rsync_remoto_ok, error = _opciones_copia(max_copias, rsync_remoto)
    if error:
        return fail(error)

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
            max_copias=max_copias,
            rsync_remoto=rsync_remoto_ok,
        )
        session.add(destino)
    return RedirectResponse("/destinos", status_code=303)


def _render_editar(request: Request, d: dict, ubicaciones: list, error: str | None):
    return templates.TemplateResponse(
        "destino_editar.html",
        {
            "request": request, "active": "destinos", "destino": d,
            "ubicaciones": ubicaciones, "protecciones": PROTECCIONES, "error": error,
        },
    )


def _destino_dict(d: Destino) -> dict:
    return {
        "id": d.id, "nombre": d.nombre, "host": d.host, "puerto": d.puerto,
        "usuario": d.usuario, "carpeta_base": d.carpeta_base, "proteccion": d.proteccion,
        "ubicacion_id": d.ubicacion_id, "max_copias": d.max_copias,
        "rsync_remoto": d.rsync_remoto or "",
    }


@router.get("/{destino_id}/editar", response_class=HTMLResponse)
async def editar_form(destino_id: int, request: Request, _: int = Depends(require_login)):
    """Edita lo que se puede cambiar sin afectar a las copias ya hechas.

    La conexión y la carpeta base no se editan aquí: cambiarlas rompería el
    layout y los históricos (--link-dest) de las tareas existentes.
    """
    with session_scope() as session:
        d = session.get(Destino, destino_id)
        if not d:
            return RedirectResponse("/destinos", status_code=303)
        datos = _destino_dict(d)
        ubicaciones = [{"id": u.id, "nombre": u.nombre} for u in _ubicaciones(session)]
    return _render_editar(request, datos, ubicaciones, None)


@router.post("/{destino_id}/editar", response_class=HTMLResponse)
async def editar(
    destino_id: int,
    request: Request,
    proteccion: str = Form("single"),
    ubicacion_id: str = Form(""),
    max_copias: int = Form(1),
    rsync_remoto: str = Form(""),
    _: int = Depends(require_login),
):
    max_copias, rsync_remoto_ok, error = _opciones_copia(max_copias, rsync_remoto)
    with session_scope() as session:
        d = session.get(Destino, destino_id)
        if not d:
            return RedirectResponse("/destinos", status_code=303)
        if error:
            datos = _destino_dict(d) | {
                "proteccion": proteccion, "max_copias": max_copias, "rsync_remoto": rsync_remoto,
                "ubicacion_id": int(ubicacion_id) if ubicacion_id else None,
            }
            ubicaciones = [{"id": u.id, "nombre": u.nombre} for u in _ubicaciones(session)]
            return _render_editar(request, datos, ubicaciones, error)
        d.proteccion = proteccion if proteccion in PROTECCIONES else d.proteccion
        d.ubicacion_id = int(ubicacion_id) if ubicacion_id else None
        d.max_copias = max_copias
        d.rsync_remoto = rsync_remoto_ok
    return RedirectResponse("/destinos", status_code=303)


@router.post("/{destino_id}/test")
async def probar(destino_id: int, _: int = Depends(require_login)):
    box = get_secret_box()
    with session_scope() as session:
        d = session.get(Destino, destino_id)
        if not d:
            return JSONResponse({"ok": False, "message": "No existe"}, status_code=404)
        target = SshTarget(d.host, d.puerto, d.usuario, d.auth_method,
                           box.decrypt(d.secret_cifrado), host_key=d.host_key)
    ok, message, learned = test_connection(target)
    # Primer uso: fijamos (pinning) la clave de host aprendida.
    if learned:
        with session_scope() as session:
            d = session.get(Destino, destino_id)
            if d and not d.host_key:
                d.host_key = learned
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
