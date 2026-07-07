"""Tests del analizador: cálculo de tamaño, histórico, huérfanos y ajustes."""
from __future__ import annotations

from app.db import session_scope
from app.models import HistoricoTamano, HostOrigen, Origen, Tarea, Volumen, Destino
from connectors.synology import SynologyConnector


def _host_con_origen(estado_origen="activo", con_tarea=False):
    """Crea host→volumen→origen (+ tarea opcional). Devuelve (host_id, origen_id)."""
    with session_scope() as s:
        host = HostOrigen(nombre="nas", tipo_conector="synology", host="h",
                          usuario="u", auth_method="password", secret_cifrado="x")
        s.add(host)
        s.flush()
        vol = Volumen(host_origen_id=host.id, nombre="volume1", proteccion="raid1")
        s.add(vol)
        s.flush()
        o = Origen(volumen_id=vol.id, nombre="web", tipo="carpeta", ruta="/volume1/web",
                   estado=estado_origen)
        s.add(o)
        s.flush()
        if con_tarea:
            d = Destino(nombre="d", host="dh", usuario="u", auth_method="password",
                        secret_cifrado="y", carpeta_base="/bk", proteccion="single")
            s.add(d)
            s.flush()
            s.add(Tarea(origen_id=o.id, destino_id=d.id, tipo="espejo"))
        return host.id, o.id


# --- Cálculo de tamaño (du) por el conector ----------------------------------

def test_medir_tamano_carpeta():
    def ejecutar(cmd):
        assert "du -sb" in cmd and "/@*" not in cmd
        return (0, "1048576\n", "")
    assert SynologyConnector().medir_tamano(ejecutar, "carpeta", "/volume1/web") == 1048576


def test_medir_tamano_config_usa_arroba():
    def ejecutar(cmd):
        assert "/@*" in cmd  # el bundle mide solo lo que empieza por @
        return (0, "2048\n", "")
    assert SynologyConnector().medir_tamano(ejecutar, "config", "/volume1") == 2048


def test_medir_tamano_falla_devuelve_none():
    assert SynologyConnector().medir_tamano(lambda c: (1, "", "err"), "carpeta", "/x") is None
    assert SynologyConnector().medir_tamano(lambda c: (0, "nope\n", ""), "carpeta", "/x") is None


# --- Registro de tamaño + histórico ------------------------------------------

def test_registrar_tamano_actualiza_y_apila_historico(client):
    from daemon.analyzer import registrar_tamano
    _, oid = _host_con_origen()
    with session_scope() as s:
        registrar_tamano(s, oid, 1000)
    with session_scope() as s:
        registrar_tamano(s, oid, 1500)
    with session_scope() as s:
        o = s.get(Origen, oid)
        assert o.tamano_bytes == 1500 and o.last_size_check is not None
        hist = s.query(HistoricoTamano).filter_by(origen_id=oid).all()
        assert sorted(h.bytes for h in hist) == [1000, 1500]  # histórico conservado


# --- Detección de huérfanos --------------------------------------------------

def test_nuevos_huerfanos_detecta_desaparecido_con_tareas(client):
    from daemon.analyzer import nuevos_huerfanos
    host_id, oid = _host_con_origen(estado_origen="desaparecido", con_tarea=True)
    with session_scope() as s:
        h = s.get(HostOrigen, host_id)
        # antes estaba activo -> ahora desaparecido y con tareas => huérfano nuevo
        avisos = nuevos_huerfanos(h, {oid: "activo"})
        assert avisos == [("nas", "web")]
        # si ya estaba desaparecido antes, no se re-notifica
        assert nuevos_huerfanos(h, {oid: "desaparecido"}) == []


def test_nuevos_huerfanos_sin_tareas_no_avisa(client):
    from daemon.analyzer import nuevos_huerfanos
    host_id, oid = _host_con_origen(estado_origen="desaparecido", con_tarea=False)
    with session_scope() as s:
        h = s.get(HostOrigen, host_id)
        assert nuevos_huerfanos(h, {oid: "activo"}) == []


# --- Ajustes -----------------------------------------------------------------

def test_settings_defaults_y_set(client):
    from app import settings
    with session_scope() as s:
        assert settings.intervalo_analizador_horas(s) == 24  # default
        settings.set_ajuste(s, settings.ANALIZADOR_INTERVALO_HORAS, "6")
    with session_scope() as s:
        assert settings.intervalo_analizador_horas(s) == 6
        assert settings.analizador_run_now(s) is False
        settings.marcar_analizador_run_now(s, True)
    with session_scope() as s:
        assert settings.analizador_run_now(s) is True


def test_ajustes_ui(auth_client):
    assert auth_client.get("/ajustes").status_code == 200
    # Guardar intervalo.
    auth_client.post("/ajustes/analizador", data={"intervalo_horas": 12}, follow_redirects=False)
    from app import settings
    with session_scope() as s:
        assert settings.intervalo_analizador_horas(s) == 12
    # Disparo manual: marca la bandera.
    auth_client.post("/ajustes/analizar", follow_redirects=False)
    with session_scope() as s:
        assert settings.analizador_run_now(s) is True
    # Alta de ubicación.
    auth_client.post("/ajustes/ubicaciones", data={"nombre": "CPD-Norte"}, follow_redirects=False)
    from app.models import Ubicacion
    with session_scope() as s:
        assert s.scalar(Ubicacion.__table__.select().where(Ubicacion.nombre == "CPD-Norte")) is not None
