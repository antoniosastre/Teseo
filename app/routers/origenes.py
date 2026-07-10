"""Gestión de hosts de origen, exploración por conector y orígenes de copia.

Flujo de alta (wizard de 2 pantallas):
  1. /origenes/host/nuevo  -> datos de conexión + conector.
  2. POST /origenes/host    -> crea host, EXPLORA con el conector, redirige a…
  3. /origenes/host/{id}/configurar -> RAID por volumen + ubicación del host.

La vista /origenes muestra la jerarquía Host → Volumen → Origen con la barra de
protección, el estado de copia y los botones Configurar/Editar por origen.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select

from app.db import session_scope
from app.deps import get_secret_box, require_login
from app.models import (
    CONECTORES,
    PROTECCIONES,
    TIPOS_TAREA,
    Destino,
    HostOrigen,
    Origen,
    Tarea,
    Ubicacion,
    Volumen,
)
from app.remote import SshError, test_connection
from app.rsync_cmd import preview_command, validate_override
from app.services import (
    estado_copia,
    evolucion_tamano,
    explorar_host,
    host_semaforo,
    opciones_conector,
    origen_score_bar,
    ssh_target_for_host,
    ultima_copia_ok,
)
from connectors import conectores_disponibles, get_connector
from app.templating import templates

router = APIRouter(prefix="/origenes")

_CONECTOR_LABEL = dict(conectores_disponibles())


# --- Vista jerárquica --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def listar(request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        hosts_data = []
        for h in session.scalars(select(HostOrigen).order_by(HostOrigen.nombre)):
            volumenes = []
            for vol in sorted(h.volumenes, key=lambda v: v.nombre):
                origenes = []
                for o in sorted(vol.origenes, key=lambda o: o.nombre):
                    sb = origen_score_bar(o)
                    origenes.append({
                        "id": o.id,
                        "nombre": o.nombre,
                        "tipo": o.tipo,
                        "estado": o.estado,
                        "tamano_bytes": o.tamano_bytes,
                        "n_tareas": len([t for t in o.tareas]),
                        "estado_copia": estado_copia(o),
                        "ultima_ok": ultima_copia_ok(o),
                        "score_pct": sb.pct,
                        "score_color": sb.color,
                        "score_texto": sb.texto,
                    })
                volumenes.append({"id": vol.id, "nombre": vol.nombre,
                                  "proteccion": vol.proteccion, "origenes": origenes})
            hosts_data.append({
                "id": h.id, "nombre": h.nombre, "host": h.host,
                "conector": _CONECTOR_LABEL.get(h.tipo_conector, h.tipo_conector),
                "semaforo": host_semaforo(h), "estado_conexion": h.estado_conexion,
                "ubicacion": h.ubicacion.nombre if h.ubicacion else "—",
                "volumenes": volumenes,
            })
        # Para el panel de creación de tareas en lote.
        destinos = [
            {"id": d.id, "nombre": d.nombre}
            for d in session.scalars(select(Destino).order_by(Destino.nombre))
        ]
    return templates.TemplateResponse(
        "origenes.html",
        {"request": request, "active": "origenes", "hosts": hosts_data,
         "destinos": destinos, "tipos": TIPOS_TAREA,
         "lote_creadas": request.query_params.get("creadas"),
         "lote_omitidas": request.query_params.get("omitidas"),
         "error": request.query_params.get("error")},
    )


# --- Wizard: pantalla 1 (alta + exploración) ---------------------------------

def _opciones_por_conector() -> dict:
    """{tipo: [ {clave, etiqueta, tipo}... ]} para pintar los campos del wizard."""
    return {
        tipo: [
            {"clave": o.clave, "etiqueta": o.etiqueta, "tipo": o.tipo}
            for o in get_connector(tipo).opciones_descubrimiento()
        ]
        for tipo, _ in conectores_disponibles()
    }


@router.get("/host/nuevo", response_class=HTMLResponse)
async def host_form(request: Request, _: int = Depends(require_login)):
    return templates.TemplateResponse(
        "host_form.html",
        {"request": request, "active": "origenes", "conectores": conectores_disponibles(),
         "opciones_por_conector": _opciones_por_conector(), "error": None, "form": {}},
    )


@router.post("/host", response_class=HTMLResponse)
async def host_crear(
    request: Request,
    nombre: str = Form(...),
    tipo_conector: str = Form(...),
    host: str = Form(...),
    puerto: int = Form(22),
    usuario: str = Form(...),
    auth_method: str = Form("password"),
    secret: str = Form(""),
    _: int = Depends(require_login),
):
    box = get_secret_box()
    form = {"nombre": nombre, "tipo_conector": tipo_conector, "host": host,
            "puerto": puerto, "usuario": usuario, "auth_method": auth_method}

    def fail(msg: str):
        return templates.TemplateResponse(
            "host_form.html",
            {"request": request, "active": "origenes", "conectores": conectores_disponibles(),
             "opciones_por_conector": _opciones_por_conector(), "error": msg, "form": form},
        )

    if tipo_conector not in CONECTORES:
        return fail("Conector no válido.")

    # Opciones de descubrimiento del conector elegido (checkboxes/textarea).
    datos = await request.form()
    opciones = {}
    for opt in get_connector(tipo_conector).opciones_descubrimiento():
        if opt.tipo == "checkbox":
            opciones[opt.clave] = datos.get(opt.clave) is not None
        else:
            opciones[opt.clave] = datos.get(opt.clave, "")

    with session_scope() as session:
        if session.scalar(select(HostOrigen).where(HostOrigen.nombre == nombre.strip())):
            return fail("Ya existe un host con ese nombre.")
        host_obj = HostOrigen(
            nombre=nombre.strip(),
            tipo_conector=tipo_conector,
            host=host.strip(),
            puerto=puerto,
            usuario=usuario.strip(),
            auth_method=auth_method if auth_method in ("key", "password") else "password",
            secret_cifrado=box.encrypt(secret),
            conector_opciones=json.dumps(opciones),
        )
        session.add(host_obj)
        session.flush()
        host_id = host_obj.id
        # Exploración con el conector (conecta por SSH y descubre volúmenes/orígenes).
        try:
            explorar_host(session, host_obj, box)
            host_obj.estado_conexion = "conectado"
        except SshError as exc:
            session.rollback()
            return fail(f"No se pudo explorar el host: {exc}")
        except NotImplementedError:
            session.rollback()
            return fail("El conector seleccionado aún no soporta el descubrimiento de orígenes.")

    return RedirectResponse(f"/origenes/host/{host_id}/configurar", status_code=303)


# --- Wizard: pantalla 2 (RAID por volumen + ubicación del host) --------------

@router.get("/host/{host_id}/configurar", response_class=HTMLResponse)
async def host_configurar_form(host_id: int, request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        if not h:
            return RedirectResponse("/origenes", status_code=303)
        volumenes = [
            {"id": v.id, "nombre": v.nombre, "dispositivo": v.dispositivo,
             "proteccion": v.proteccion, "n_origenes": len(v.origenes)}
            for v in sorted(h.volumenes, key=lambda v: v.nombre)
        ]
        ubicaciones = [{"id": u.id, "nombre": u.nombre}
                       for u in session.scalars(select(Ubicacion).order_by(Ubicacion.nombre))]
        # Rutas adicionales indicadas que no se encontraron (no generaron origen).
        rutas_pedidas = [ln.strip() for ln in
                         str(opciones_conector(h).get("rutas_extra", "")).splitlines() if ln.strip()]
        rutas_existentes = {o.ruta for v in h.volumenes for o in v.origenes}
        rutas_faltantes = [r for r in rutas_pedidas if r not in rutas_existentes]
        data = {"id": h.id, "nombre": h.nombre, "ubicacion_id": h.ubicacion_id}
    return templates.TemplateResponse(
        "host_config.html",
        {"request": request, "active": "origenes", "host": data, "volumenes": volumenes,
         "ubicaciones": ubicaciones, "protecciones": PROTECCIONES, "rutas_faltantes": rutas_faltantes},
    )


@router.post("/host/{host_id}/configurar")
async def host_configurar(
    host_id: int, request: Request, _: int = Depends(require_login)
):
    form = await request.form()
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        if not h:
            return RedirectResponse("/origenes", status_code=303)
        ubic = form.get("ubicacion_id")
        h.ubicacion_id = int(ubic) if ubic else None
        for vol in h.volumenes:
            valor = form.get(f"proteccion_{vol.id}")
            if valor in PROTECCIONES:
                vol.proteccion = valor
    return RedirectResponse("/origenes", status_code=303)


@router.post("/host/{host_id}/eliminar")
async def host_eliminar(host_id: int, _: int = Depends(require_login)):
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        if h:
            session.delete(h)  # cascada: volúmenes, orígenes y tareas
    return RedirectResponse("/origenes", status_code=303)


@router.post("/origen/{origen_id}/eliminar")
async def origen_eliminar(origen_id: int, _: int = Depends(require_login)):
    """Elimina un origen SOLO si está "desaparecido" (huérfano tras la re-exploración).

    Los orígenes activos los gobierna el descubrimiento del conector; borrarlos a mano
    solo generaría su re-creación. Cascada: tareas, ejecuciones e histórico de tamaño.
    """
    with session_scope() as session:
        o = session.get(Origen, origen_id)
        if o and o.estado == "desaparecido":
            session.delete(o)
    return RedirectResponse("/origenes", status_code=303)


@router.post("/host/{host_id}/test")
async def host_test(host_id: int, _: int = Depends(require_login)):
    box = get_secret_box()
    with session_scope() as session:
        h = session.get(HostOrigen, host_id)
        if not h:
            return JSONResponse({"ok": False, "message": "No existe"}, status_code=404)
        target = ssh_target_for_host(h, box)
    ok, message, learned = test_connection(target)
    # Primer uso: fijamos (pinning) la clave de host aprendida para futuras conexiones.
    if learned:
        with session_scope() as session:
            h = session.get(HostOrigen, host_id)
            if h and not h.host_key:
                h.host_key = learned
    return JSONResponse({"ok": ok, "message": message})


# --- Gestión de tareas de un origen ------------------------------------------

@router.get("/origen/{origen_id}", response_class=HTMLResponse)
async def origen_detalle(origen_id: int, request: Request, _: int = Depends(require_login)):
    with session_scope() as session:
        o = session.get(Origen, origen_id)
        if not o:
            return RedirectResponse("/origenes", status_code=303)
        sb = origen_score_bar(o)
        tareas = [{
            "id": t.id, "tipo": t.tipo, "destino": t.destino.nombre, "cron": t.cron,
            "estado": t.estado, "porcentaje": t.porcentaje, "activa": t.activa,
            "retencion_dias": t.retencion_dias, "last_run_at": t.last_run_at,
            "next_run_at": t.next_run_at,
        } for t in o.tareas]
        destinos = [{"id": d.id, "nombre": d.nombre}
                    for d in session.scalars(select(Destino).order_by(Destino.nombre))]
        muestras, evolucion = evolucion_tamano(o)
        data = {
            "id": o.id, "nombre": o.nombre, "tipo": o.tipo, "ruta": o.ruta,
            "estado": o.estado, "tamano_bytes": o.tamano_bytes,
            "volumen": o.volumen.nombre, "host": o.volumen.host_origen.nombre,
            "score_pct": sb.pct, "score_color": sb.color, "score_texto": sb.texto,
            "tareas": tareas,
        }
    return templates.TemplateResponse(
        "origen_detalle.html",
        {"request": request, "active": "origenes", "origen": data, "destinos": destinos,
         "tipos": TIPOS_TAREA, "muestras": muestras, "evolucion": evolucion,
         "error": request.query_params.get("error")},
    )


@router.post("/origen/{origen_id}/tarea")
async def tarea_crear(
    origen_id: int,
    destino_id: int = Form(...),
    tipo: str = Form("espejo"),
    cron: str = Form("0 2 * * *"),
    retencion_dias: int = Form(7),
    rsync_extra: str = Form(""),
    comando_rsync: str = Form(""),
    _: int = Depends(require_login),
):
    def fail(msg: str):
        return RedirectResponse(f"/origenes/origen/{origen_id}?error={msg}", status_code=303)

    from croniter import croniter
    try:
        if not croniter.is_valid(cron):
            return fail("cron-invalido")
    except Exception:  # noqa: BLE001
        return fail("cron-invalido")

    if validate_override(comando_rsync):
        return fail("override-invalido")

    # Validación del override manual (si lo hay): debe ser una invocación rsync
    # sin metacaracteres de shell (evita inyección de comandos en el origen).
    override_error = validate_override(comando_rsync)
    if override_error:
        return fail(override_error)

    with session_scope() as session:
        o = session.get(Origen, origen_id)
        d = session.get(Destino, destino_id)
        if not o or not d:
            return fail("origen-o-destino-invalido")
        dup = session.scalar(
            select(Tarea).where(
                Tarea.origen_id == origen_id, Tarea.destino_id == destino_id, Tarea.tipo == tipo,
            )
        )
        if dup:
            return fail("tarea-duplicada")
        session.add(Tarea(
            origen_id=origen_id,
            destino_id=destino_id,
            tipo=tipo if tipo in TIPOS_TAREA else "espejo",
            cron=cron.strip(),
            retencion_dias=max(1, retencion_dias),
            rsync_extra=rsync_extra or None,
            comando_rsync=comando_rsync.strip() or None,
        ))
    return RedirectResponse(f"/origenes/origen/{origen_id}", status_code=303)


@router.post("/tareas-lote")
async def tareas_lote(
    origen_ids: list[int] = Form([]),
    destino_id: int = Form(...),
    tipo: str = Form("espejo"),
    cron: str = Form("0 2 * * *"),
    retencion_dias: int = Form(7),
    _: int = Depends(require_login),
):
    """Crea una tarea individual por cada origen seleccionado, con los mismos
    destino/tipo/cron/retención. Omite duplicados y orígenes desaparecidos."""
    from croniter import croniter

    def fail(msg: str):
        return RedirectResponse(f"/origenes?error={msg}", status_code=303)

    if not origen_ids:
        return fail("lote-sin-origenes")
    try:
        if not croniter.is_valid(cron):
            return fail("cron-invalido")
    except Exception:  # noqa: BLE001
        return fail("cron-invalido")

    creadas = omitidas = 0
    with session_scope() as session:
        d = session.get(Destino, destino_id)
        if not d:
            return fail("origen-o-destino-invalido")
        for oid in origen_ids:
            o = session.get(Origen, oid)
            if not o or o.estado == "desaparecido":
                omitidas += 1
                continue
            dup = session.scalar(
                select(Tarea).where(
                    Tarea.origen_id == oid, Tarea.destino_id == destino_id, Tarea.tipo == tipo,
                )
            )
            if dup:
                omitidas += 1
                continue
            session.add(Tarea(
                origen_id=oid,
                destino_id=destino_id,
                tipo=tipo if tipo in TIPOS_TAREA else "espejo",
                cron=cron.strip(),
                retencion_dias=max(1, retencion_dias),
            ))
            creadas += 1
    return RedirectResponse(f"/origenes?creadas={creadas}&omitidas={omitidas}", status_code=303)


@router.post("/preview")
async def preview(
    origen_id: int = Form(...),
    destino_id: int = Form(...),
    tipo: str = Form("espejo"),
    rsync_extra: str = Form(""),
    _: int = Depends(require_login),
):
    with session_scope() as session:
        o = session.get(Origen, origen_id)
        d = session.get(Destino, destino_id)
        if not o or not d:
            return JSONResponse({"error": "Origen o destino inválido"}, status_code=400)
        host = o.volumen.host_origen
        conector = get_connector(host.tipo_conector)
        ruta_origen, filtros = conector.fuente_rsync(o.tipo, o.ruta)
        cmd = preview_command(
            ruta_origen=ruta_origen, carpeta_base=d.carpeta_base, host_nombre=host.nombre,
            volumen_nombre=o.volumen.nombre, origen_nombre=o.nombre, tipo=tipo,
            destino_usuario=d.usuario, destino_host=d.host, destino_puerto=d.puerto,
            extra_flags=rsync_extra or None, filtros=filtros,
        )
    return JSONResponse({"command": cmd})
