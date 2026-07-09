"""Tests de integración de la web (sobre SQLite en memoria)."""
from __future__ import annotations

from app.db import session_scope
from app.models import Destino, HostOrigen, Origen, Tarea, Ubicacion, Volumen


def _crear_entorno(host_ubic="CPD-A", destino_ubic="CPD-B", vol_raid="raid1", dest_raid="raid2"):
    """Crea host→volumen→origen + destino directamente en BD. Devuelve (origen_id, destino_id)."""
    with session_scope() as s:
        ua = Ubicacion(nombre=host_ubic)
        ub = Ubicacion(nombre=destino_ubic)
        s.add_all([ua, ub])
        s.flush()
        host = HostOrigen(nombre="web1", tipo_conector="synology", host="10.0.0.5",
                          usuario="root", auth_method="password", secret_cifrado="x",
                          ubicacion_id=ua.id, estado_conexion="conectado")
        s.add(host)
        s.flush()
        vol = Volumen(host_origen_id=host.id, nombre="volume1", proteccion=vol_raid)
        s.add(vol)
        s.flush()
        origen = Origen(volumen_id=vol.id, nombre="web", tipo="carpeta", ruta="/volume1/web")
        s.add(origen)
        s.flush()
        destino = Destino(nombre="nas1", host="10.0.0.9", usuario="bk", auth_method="password",
                          secret_cifrado="y", carpeta_base="/backups", proteccion=dest_raid,
                          ubicacion_id=ub.id)
        s.add(destino)
        s.flush()
        return origen.id, destino.id


def test_login_required(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_invalido(client):
    r = client.post("/login", data={"username": "admin", "password": "mal"}, follow_redirects=False)
    assert r.status_code == 200 and "incorrect" in r.text.lower()


def test_destino_cifra_secreto(auth_client):
    auth_client.post("/destinos", data={
        "nombre": "nas1", "host": "10.0.0.9", "puerto": 22, "usuario": "bk",
        "auth_method": "password", "secret": "supersecreto", "carpeta_base": "/backups",
        "proteccion": "raid2"}, follow_redirects=False)
    with session_scope() as s:
        d = s.query(Destino).first()
        assert d.secret_cifrado and d.secret_cifrado != "supersecreto"


def test_crear_tarea_y_cron_invalido(auth_client):
    oid, did = _crear_entorno()
    ok = auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "incremental", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    assert ok.status_code == 303 and "error" not in ok.headers["location"]
    bad = auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "no-valido", "retencion_dias": 3},
        follow_redirects=False)
    assert bad.status_code == 303 and "cron-invalido" in bad.headers["location"]


def test_override_rsync_invalido_se_rechaza(auth_client):
    oid, did = _crear_entorno()
    bad = auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 3,
        "comando_rsync": "rsync -a /a/ u@h:/b/; rm -rf /"}, follow_redirects=False)
    assert "override-invalido" in bad.headers["location"]


def test_scoring_por_origen(auth_client):
    oid, did = _crear_entorno(vol_raid="raid1", dest_raid="raid2")
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "incremental", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    from app.services import origen_score_bar
    with session_scope() as s:
        o = s.get(Origen, oid)
        sb = origen_score_bar(o)
    # raid1 volumen (1) + tiene copia (1) + raid2 destino (2) + ubicación distinta (1) = 5
    assert sb.puntos == 5 and sb.texto == "excelente"
    assert sb.color == "azul" and sb.pct == 90


def test_origenes_renderiza_jerarquia_y_barra(auth_client):
    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    html = auth_client.get("/origenes").text
    assert "web1" in html and "volume1" in html            # jerarquía host→volumen
    assert "scorebar-fill azul" in html and "width: 90%" in html


def test_run_now_y_estado(auth_client):
    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    with session_scope() as s:
        tid = s.query(Tarea).first().id
    r = auth_client.post(f"/tareas/{tid}/run", follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        assert s.query(Tarea).first().run_now is True
    j = auth_client.get("/estado/json").json()
    assert str(tid) in j["tareas"]
    assert "velocidad" in j["tareas"][str(tid)]      # expuesta para la UI en vivo


def test_dashboard_muestra_velocidad_en_progreso(auth_client):
    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    with session_scope() as s:
        t = s.query(Tarea).first()
        t.estado, t.porcentaje, t.velocidad = "en_progreso", 42, "4.72MB/s"
    html = auth_client.get("/").text
    assert "data-tarea-vel" in html and "4.72MB/s" in html


def test_cancelar_marca_bandera_solo_en_progreso(auth_client):
    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    with session_scope() as s:
        tid = s.query(Tarea).first().id
    # Tarea "esperando": cancelar no debe marcar nada.
    auth_client.post(f"/tareas/{tid}/cancelar", follow_redirects=False)
    with session_scope() as s:
        assert s.query(Tarea).first().cancel_requested is False
    # Tarea "en_progreso": cancelar sí marca la bandera.
    with session_scope() as s:
        s.query(Tarea).first().estado = "en_progreso"
    r = auth_client.post(f"/tareas/{tid}/cancelar", follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        assert s.query(Tarea).first().cancel_requested is True
    # El estado en vivo expone la bandera para que la UI muestre "cancelando…".
    j = auth_client.get("/estado/json").json()
    assert j["tareas"][str(tid)]["cancelando"] is True
    # Y la página de la tarea la refleja ya en el render inicial.
    assert "cancelando…" in auth_client.get(f"/tareas/{tid}").text


def test_finalize_cancelada_vuelve_a_esperando(auth_client):
    """Cancelar es deliberado: la tarea vuelve a 'esperando', no queda 'fallida'."""
    import datetime as dt

    from app.models import Ejecucion
    from daemon.runner import _finalize

    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    with session_scope() as s:
        t = s.query(Tarea).first()
        tid = t.id
        t.estado = "en_progreso"
        t.porcentaje = 43
        t.cancel_requested = True
        e = Ejecucion(tarea_id=tid, inicio=dt.datetime.now())
        s.add(e)
        s.flush()
        eid = e.id
    _finalize(tid, eid, "cancelada", "Copia cancelada por el usuario.", None, None, "0 2 * * *")
    with session_scope() as s:
        t = s.query(Tarea).first()
        assert t.estado == "esperando" and t.porcentaje == 0
        assert t.cancel_requested is False          # bandera consumida
        assert t.next_run_at is not None            # vuelve a la cola
        e = s.query(Ejecucion).first()
        assert e.resultado == "cancelada" and e.resumen == "Copia cancelada."


def test_dashboard_lista_todas_las_tareas(auth_client):
    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    html = auth_client.get("/").text
    assert "Tareas de copia" in html
    assert "web1 · web" in html and "nas1" in html      # origen y destino
    assert "data-tarea-estado" in html                   # badges vivos por SSE
    assert "data-tarea-bar" in html                      # barra de progreso viva


def test_finalize_parcial_marca_terminada(auth_client):
    """rsync 23 (transferencia parcial) => tarea terminada, ejecución 'parcial', sin fallo."""
    import datetime as dt

    from app.models import Ejecucion
    from daemon.runner import _finalize

    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    with session_scope() as s:
        t = s.query(Tarea).first()
        tid = t.id
        t.estado = "en_progreso"
        t.velocidad = "4.72MB/s"
        e = Ejecucion(tarea_id=tid, inicio=dt.datetime.now())
        s.add(e)
        s.flush()
        eid = e.id
    _finalize(tid, eid, "parcial", "cola del log con avisos", 1234, None, "0 2 * * *")
    with session_scope() as s:
        t = s.query(Tarea).first()
        assert t.estado == "terminada" and t.porcentaje == 100
        assert t.velocidad is None                   # la copia ya no corre
        e = s.query(Ejecucion).first()
        assert e.resultado == "parcial"
        assert "avisos" in e.resumen
        assert e.bytes_transferidos == 1234


def test_eliminar_origen_solo_si_desaparecido(auth_client):
    """Un origen activo no se puede borrar a mano; uno huérfano sí (con cascada)."""
    from app.models import Ejecucion, Origen

    oid, did = _crear_entorno()
    auth_client.post(f"/origenes/origen/{oid}/tarea", data={
        "destino_id": did, "tipo": "espejo", "cron": "0 2 * * *", "retencion_dias": 5},
        follow_redirects=False)
    # Activo: el POST no borra nada.
    auth_client.post(f"/origenes/origen/{oid}/eliminar", follow_redirects=False)
    with session_scope() as s:
        assert s.get(Origen, oid) is not None
    # Desaparecido: se borra con sus tareas (cascada).
    with session_scope() as s:
        s.get(Origen, oid).estado = "desaparecido"
    r = auth_client.post(f"/origenes/origen/{oid}/eliminar", follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        assert s.get(Origen, oid) is None
        assert s.query(Tarea).count() == 0


def test_pantallas_wizard_y_detalle_renderizan(auth_client):
    oid, did = _crear_entorno()
    with session_scope() as s:
        host_id = s.query(HostOrigen).first().id
    # Todas deben renderizar sin errores de plantilla (200).
    assert auth_client.get("/origenes/host/nuevo").status_code == 200
    assert auth_client.get(f"/origenes/host/{host_id}/configurar").status_code == 200
    assert auth_client.get(f"/origenes/origen/{oid}").status_code == 200


def test_persistir_descubrimiento_crea_y_marca_huerfanos(client):
    """El descubrimiento crea orígenes; los que desaparecen se marcan (no se borran)."""
    from app.services import persistir_descubrimiento
    from connectors import OrigenDescubierto, VolumenDescubierto

    with session_scope() as s:
        host = HostOrigen(nombre="nas", tipo_conector="synology", host="h",
                          usuario="u", auth_method="password", secret_cifrado="x")
        s.add(host)
        s.flush()
        # Primer descubrimiento: volume1 con "Configuración" y "web".
        persistir_descubrimiento(s, host, [
            VolumenDescubierto("volume1", origenes=[
                OrigenDescubierto("Configuración", "config", "/volume1"),
                OrigenDescubierto("web", "carpeta", "/volume1/web"),
            ])
        ])
        s.flush()
        assert {o.nombre for v in host.volumenes for o in v.origenes} == {"Configuración", "web"}

        # Segundo descubrimiento: "web" ha desaparecido.
        persistir_descubrimiento(s, host, [
            VolumenDescubierto("volume1", origenes=[
                OrigenDescubierto("Configuración", "config", "/volume1"),
            ])
        ])
        s.flush()
        estados = {o.ruta: o.estado for v in host.volumenes for o in v.origenes}
        assert estados["/volume1/web"] == "desaparecido"   # marcado, no borrado
        assert estados["/volume1"] == "activo"
