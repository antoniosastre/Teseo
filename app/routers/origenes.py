"""Gestión de hosts origen y de orígenes (tareas de backup)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import get_secret_box, require_login
from app.models import PROTECCIONES, TIPOS_TAREA, Destino, HostOrigen, Tarea, Ubicacion
from app.remote import SshError, SshTarget, list_directories, test_connection
from app.rsync_cmd import preview_command
from app.services import host_semaforo, ssh_target_for_host, tarea_score
from app.templating import templates

router = APIRouter(prefix="/origenes")


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        hosts = list(session.scalars(select(HostOrigen).order_by(HostOrigen.nombre)))
        rows = []
        for h in hosts:
            tareas = []
            for t in h.tareas:
                sb = tarea_score(t)
                tareas.append(
                    {
                        "id": t.id,
                        "carpeta_origen": t.carpeta_origen,
                        "tipo": t.tipo,
                        "estado": t.estado,
                        "porcentaje": t.porcentaje,
                        "destino": t.destino.nombre,
                        "last_run_at": t.last_run_at,
                        "next_run_at": t.next_run_at,
                        "activa": t.activa,
                        "score_pct": sb.pct,
                        "score_color": sb.color,
                        "score_texto": sb.texto,
                    }
                )
            rows.append(
                {
                    "id": h.id,
                    "nombre": h.nombre,
                    "host": h.host,
                    "semaforo": host_semaforo(h),
                    "estado_conexion": h.estado_conexion,
                    "es_raid": h.es_raid,
                    "ubicacion": h.ubicacion.nombre if h.ubicacion else "—",
                    "tareas": tareas,
                }
            )
    return templates.TemplateResponse(
        "origenes.html", {"request": request, "active": "origenes", "hosts": rows}
    )


# --- Alta de host origen -----------------------------------------------------

@router.get("/host/nuevo", response_class=HTMLResponse)
async def host_form(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        ubicaciones = [{"id": u.id, "nombre": u.nombre} for u in session.scalars(select(Ubicacion).order_by(Ubicacion.nombre))]
    return templates.TemplateResponse(
        "host_form.html",
        {
            "request": request, "active": "origenes", "ubicaciones": ubicaciones,
            "protecciones": PROTECCIONES, "error": None, "form": {},
        },
    )


@router.post("/host", response_class=HTMLResponse)
async def host_crear(
    request: Request,
    nombre: str = Form(...),
    host: str = Form(...),
    puerto: int = Form(22),
    usuario: str = Form(...),
    auth_method: str = Form("password"),
    secret: str = Form(""),
    es_raid: str = Form("single"),
    ubicacion_id: str = Form(""),
    _: int = Depends(require_login),
):
    box = get_secret_box()
    form = {
        "nombre": nombre, "host": host, "puerto": puerto, "usuario": usuario,
        "auth_method": auth_method, "es_raid": es_raid, "ubicacion_id": ubicacion_id,
    }

    def fail(msg: str):
        with session_scope() as session:
            ubicaciones = [{"id": u.id, "nombre": u.nombre} for u in session.scalars(select(Ubicacion).order_by(Ubicacion.nombre))]
        return templates.TemplateResponse(
            "host_form.html",
            {"request": request, "active": "origenes", "ubicaciones": ubicaciones,
             "protecciones": PROTECCIONES, "error": msg, "form": form},
        )

    with session_scope() as session:
        if session.scalar(select(HostOrigen).where(HostOrigen.nombre == nombre.strip())):
            return fail("Ya existe un host con ese nombre.")
        session.add(
            HostOrigen(
                nombre=nombre.strip(),
                host=host.strip(),
                puerto=puerto,
                usuario=usuario.strip(),
                auth_method=auth_method if auth_method in ("key", "password") else "password",
                secret_cifrado=box.encrypt(secret),
                es_raid=es_raid if es_raid in PROTECCIONES else "single",
                ubicacion_id=int(ubicacion_id) if ubicacion_id else None,
            )
        )
    return RedirectResponse("/origenes", status_code=303)


@router.post("/host/{host_id}/test")
async def host_test(host_id: int, _: int = Depends(require_login)):
    box = get_secret_box()
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        if not h:
            return JSONResponse({"ok": False, "message": "No existe"}, status_code=404)
        target = ssh_target_for_host(h, box)
    ok, message = test_connection(target)
    return JSONResponse({"ok": ok, "message": message})


@router.get("/host/{host_id}/carpetas")
async def host_carpetas(host_id: int, path: str = "/", _: int = Depends(require_login)):
    """Lista subdirectorios del host para el selector de carpeta a copiar."""
    box = get_secret_box()
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        if not h:
            return JSONResponse({"error": "No existe"}, status_code=404)
        target = ssh_target_for_host(h, box)
    try:
        dirs = list_directories(target, path)
        return JSONResponse({"path": path, "dirs": dirs})
    except SshError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.post("/host/{host_id}/eliminar")
async def host_eliminar(host_id: int, _: int = Depends(require_login)):
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        if h:
            session.delete(h)  # cascada elimina sus tareas
    return RedirectResponse("/origenes", status_code=303)


# --- Alta de origen (tarea) --------------------------------------------------

@router.get("/nuevo", response_class=HTMLResponse)
async def tarea_form(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        hosts = [{"id": h.id, "nombre": h.nombre} for h in session.scalars(select(HostOrigen).order_by(HostOrigen.nombre))]
        destinos = [{"id": d.id, "nombre": d.nombre} for d in session.scalars(select(Destino).order_by(Destino.nombre))]
    return templates.TemplateResponse(
        "tarea_form.html",
        {
            "request": request, "active": "origenes",
            "hosts": hosts, "destinos": destinos, "tipos": TIPOS_TAREA,
            "error": None, "form": {},
        },
    )


@router.post("/preview")
async def preview(
    host_id: int = Form(...),
    destino_id: int = Form(...),
    carpeta_origen: str = Form(...),
    tipo: str = Form("espejo"),
    rsync_extra: str = Form(""),
    _: int = Depends(require_login),
):
    """Devuelve el comando rsync por defecto para 'opciones avanzadas'."""
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        d = session.get(Destino, destino_id)
        if not h or not d:
            return JSONResponse({"error": "Host o destino inválido"}, status_code=400)
        cmd = preview_command(
            carpeta_origen=carpeta_origen,
            carpeta_base=d.carpeta_base,
            host_nombre=h.nombre,
            tipo=tipo,
            destino_usuario=d.usuario,
            destino_host=d.host,
            destino_puerto=d.puerto,
            extra_flags=rsync_extra or None,
        )
    return JSONResponse({"command": cmd})


@router.post("", response_class=HTMLResponse)
async def tarea_crear(
    request: Request,
    host_id: int = Form(...),
    destino_id: int = Form(...),
    carpeta_origen: str = Form(...),
    tipo: str = Form("espejo"),
    cron: str = Form("0 2 * * *"),
    retencion: int = Form(7),
    rsync_extra: str = Form(""),
    comando_rsync: str = Form(""),
    _: int = Depends(require_login),
):
    form = {
        "host_id": host_id, "destino_id": destino_id, "carpeta_origen": carpeta_origen,
        "tipo": tipo, "cron": cron, "retencion": retencion,
        "rsync_extra": rsync_extra, "comando_rsync": comando_rsync,
    }

    def fail(msg: str):
        with session_scope() as session:
            hosts = [{"id": h.id, "nombre": h.nombre} for h in session.scalars(select(HostOrigen).order_by(HostOrigen.nombre))]
            destinos = [{"id": d.id, "nombre": d.nombre} for d in session.scalars(select(Destino).order_by(Destino.nombre))]
        return templates.TemplateResponse(
            "tarea_form.html",
            {"request": request, "active": "origenes", "hosts": hosts, "destinos": destinos,
             "tipos": TIPOS_TAREA, "error": msg, "form": form},
        )

    # Validación del cron.
    try:
        from croniter import croniter

        if not croniter.is_valid(cron):
            return fail("La expresión de programación (cron) no es válida.")
    except Exception:  # noqa: BLE001
        return fail("La expresión de programación (cron) no es válida.")

    with session_scope() as session:
        if not session.get(HostOrigen, host_id) or not session.get(Destino, destino_id):
            return fail("Host o destino inválido.")
        dup = session.scalar(
            select(Tarea).where(
                Tarea.host_origen_id == host_id,
                Tarea.destino_id == destino_id,
                Tarea.carpeta_origen == carpeta_origen.strip(),
            )
        )
        if dup:
            return fail("Ya existe una tarea para esa carpeta en ese destino.")
        session.add(
            Tarea(
                host_origen_id=host_id,
                destino_id=destino_id,
                carpeta_origen=carpeta_origen.strip(),
                tipo=tipo if tipo in TIPOS_TAREA else "espejo",
                cron=cron.strip(),
                retencion=max(1, retencion),
                rsync_extra=rsync_extra or None,
                comando_rsync=comando_rsync.strip() or None,
            )
        )
    return RedirectResponse("/origenes", status_code=303)
