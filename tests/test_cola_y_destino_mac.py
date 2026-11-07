"""Tests de la cola por destino/origen, destinos macOS y retención descolgada."""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import time

import pytest

from app.db import session_scope
from app.models import Destino, HostOrigen, Origen, Tarea, Volumen
from app.remote import SshError, parse_df_posix
from app.rsync_cmd import build_plan, validate_rsync_remoto
from daemon.retention import _PREFIJO_BORRANDO, comando_retencion
from daemon.teseod import Candidata, Daemon, elegir_lanzables


# --- Cola: función pura -------------------------------------------------------

def _c(tid, destino, host):
    return Candidata(tarea_id=tid, destino_id=destino, host_id=host)


def test_cola_una_copia_por_destino():
    elegidas = elegir_lanzables(
        [], [_c(1, 10, 100), _c(2, 10, 101)],
        max_total=3, max_por_destino={10: 1}, max_por_origen=5,
    )
    assert elegidas == [1]   # la 2 escribe en el mismo destino: espera turno


def test_cola_saltar_no_corta_la_cola():
    # La 2 no cabe en su destino, pero la 3 (otro destino) sí debe lanzarse.
    elegidas = elegir_lanzables(
        [], [_c(1, 10, 100), _c(2, 10, 101), _c(3, 20, 102)],
        max_total=3, max_por_destino={10: 1, 20: 1}, max_por_origen=5,
    )
    assert elegidas == [1, 3]


def test_cola_limite_por_origen():
    elegidas = elegir_lanzables(
        [], [_c(1, 10, 100), _c(2, 20, 100)],
        max_total=3, max_por_destino={10: 1, 20: 1}, max_por_origen=1,
    )
    assert elegidas == [1]   # mismo host de origen: una lectura cada vez


def test_cola_cuenta_las_copias_vivas():
    # Ya hay una copia viva en el destino 10 desde el host 100.
    elegidas = elegir_lanzables(
        [(10, 100)], [_c(1, 10, 101), _c(2, 20, 100), _c(3, 20, 102)],
        max_total=3, max_por_destino={10: 1, 20: 1}, max_por_origen=1,
    )
    assert elegidas == [3]


def test_cola_respeta_el_maximo_total_y_destinos_con_mas_hueco():
    cands = [_c(i, 10, 100 + i) for i in range(1, 6)]
    assert elegir_lanzables([], cands, max_total=3, max_por_destino={10: 4},
                            max_por_origen=1) == [1, 2, 3]
    assert elegir_lanzables([], cands, max_total=5, max_por_destino={10: 2},
                            max_por_origen=1) == [1, 2]


# --- Cola: integración con la BD ----------------------------------------------

def _entorno_cola(n_origenes=2, max_copias=1):
    """Host con n orígenes, un destino y una tarea vencida por origen."""
    with session_scope() as s:
        host = HostOrigen(nombre="nas", tipo_conector="synology", host="10.0.0.5",
                          usuario="root", auth_method="password", secret_cifrado="x")
        s.add(host)
        s.flush()
        vol = Volumen(host_origen_id=host.id, nombre="volume1", proteccion="raid1")
        s.add(vol)
        s.flush()
        destino = Destino(nombre="mac", host="10.0.0.9", usuario="bk", auth_method="password",
                          secret_cifrado="y", carpeta_base="/Volumes/RAID", max_copias=max_copias)
        s.add(destino)
        s.flush()
        ids = []
        for i in range(n_origenes):
            o = Origen(volumen_id=vol.id, nombre=f"c{i}", tipo="carpeta", ruta=f"/volume1/c{i}")
            s.add(o)
            s.flush()
            t = Tarea(origen_id=o.id, destino_id=destino.id, tipo="incremental",
                      cron="0 2 * * *", next_run_at=dt.datetime(2026, 1, 1, 2, 0))
            s.add(t)
            s.flush()
            ids.append(t.id)
        return ids, destino.id


@pytest.fixture()
def daemon_registro(client, monkeypatch):
    """Daemon cuyo _arrancar_worker solo registra lo que lanzaría."""
    d = Daemon()
    lanzadas = []
    monkeypatch.setattr(d, "_arrancar_worker", lambda kind, tid: lanzadas.append((kind, tid)) or True)
    return d, lanzadas


def test_daemon_lanza_de_una_en_una_al_mismo_destino(daemon_registro):
    d, lanzadas = daemon_registro
    ids, _ = _entorno_cola(n_origenes=3)
    d._lanzar_pendientes(dt.datetime(2026, 1, 1, 3, 0))
    assert lanzadas == [("launch", ids[0])]


def test_daemon_espera_si_el_destino_esta_ocupado(daemon_registro):
    d, lanzadas = daemon_registro
    ids, _ = _entorno_cola(n_origenes=2)
    with session_scope() as s:
        s.get(Tarea, ids[0]).estado = "en_progreso"
    d._lanzar_pendientes(dt.datetime(2026, 1, 1, 3, 0))
    assert lanzadas == []


def test_daemon_cuenta_las_que_se_estan_lanzando(daemon_registro):
    # Lanzada en un tick anterior pero aún no marcada "en_progreso" en la BD.
    d, lanzadas = daemon_registro
    ids, _ = _entorno_cola(n_origenes=2)
    d._lanzando.add(ids[0])
    d._lanzar_pendientes(dt.datetime(2026, 1, 1, 3, 0))
    assert lanzadas == []


def test_daemon_ejecutar_ya_tiene_prioridad(daemon_registro):
    d, lanzadas = daemon_registro
    ids, _ = _entorno_cola(n_origenes=3)
    with session_scope() as s:
        s.get(Tarea, ids[2]).run_now = True
    d._lanzar_pendientes(dt.datetime(2026, 1, 1, 3, 0))
    assert lanzadas == [("launch", ids[2])]


def test_daemon_destino_con_mas_copias_y_ajuste_por_origen(daemon_registro):
    from app.settings import MAX_COPIAS_POR_ORIGEN, set_ajuste

    d, lanzadas = daemon_registro
    ids, _ = _entorno_cola(n_origenes=3, max_copias=2)
    # Mismo host para todas: con 1 por origen solo sale una...
    d._lanzar_pendientes(dt.datetime(2026, 1, 1, 3, 0))
    assert [t for _, t in lanzadas] == [ids[0]]
    # ...y con 2 por origen salen dos (el destino admite 2).
    lanzadas.clear()
    with session_scope() as s:
        set_ajuste(s, MAX_COPIAS_POR_ORIGEN, "2")
    d._lanzar_pendientes(dt.datetime(2026, 1, 1, 3, 0))
    assert [t for _, t in lanzadas] == ids[:2]


# --- df portable (Linux y macOS) --------------------------------------------

def test_parse_df_linux():
    out = ("Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
           "/dev/md2       11546007744 9876543210 1669464534      86% /volume1\n")
    assert parse_df_posix(out) == (11546007744 * 1024, 1669464534 * 1024)


def test_parse_df_macos_con_espacios_en_el_montaje():
    out = ("Filesystem    1024-blocks    Used   Available Capacity  Mounted on\n"
           "/dev/disk11s1 41015426816     832 41011519136     1%    /Volumes/LaCie RAID VD 0\n")
    assert parse_df_posix(out) == (41015426816 * 1024, 41011519136 * 1024)


def test_parse_df_basura_devuelve_none():
    # Lo que imprime el df de macOS ante las opciones de GNU (-B1 --output): su ayuda.
    assert parse_df_posix("usage: df [-b | -g | -H | -h | -k | -m | -P] [-acilnY] [-T type]\n"
                          "          [file | filesystem ...]\n") is None
    assert parse_df_posix("") is None


# --- rsync del destino (--rsync-path) -----------------------------------------

def _plan(**kw):
    base = dict(ruta_origen="/volume1/web", carpeta_base="/Volumes/RAID", host_nombre="nas",
                volumen_nombre="volume1", origen_nombre="web", tipo="incremental",
                destino_usuario="bk", destino_host="mac", key_path="/k")
    base.update(kw)
    return build_plan(**base)


def test_rsync_remoto_se_pasa_con_rsync_path():
    cmd = _plan(rsync_remoto="/usr/local/bin/rsync").command
    assert "--rsync-path=/usr/local/bin/rsync" in cmd


def test_sin_rsync_remoto_no_hay_rsync_path():
    assert "--rsync-path" not in _plan().command
    assert "--rsync-path" not in _plan(rsync_remoto="  ").command


def test_rsync_remoto_invalido_se_ignora_al_construir():
    # Defensa en profundidad: aunque llegara a la BD, no entra en el comando.
    assert "--rsync-path" not in _plan(rsync_remoto="/usr/bin/rsync; rm -rf /").command


@pytest.mark.parametrize("ruta,valida", [
    ("", True),
    ("/usr/local/bin/rsync", True),
    ("/opt/homebrew/bin/rsync", True),
    ("rsync", False),                    # relativa
    ("/usr/bin/rsync --fake-super", False),
    ("/usr/bin/rsync;id", False),
    ("/usr/bin/$(id)", False),
    ("/usr/../bin/rsync", False),
])
def test_validate_rsync_remoto(ruta, valida):
    assert (validate_rsync_remoto(ruta) is None) is valida


# --- Retención descolgada -----------------------------------------------------

_SNAPSHOT_RE = re.compile(r".*/[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$")  # el de apply_retention


def test_apartados_no_parecen_snapshots():
    assert _SNAPSHOT_RE.match("/b/t/2026-01-01_000000")
    assert not _SNAPSHOT_RE.match(f"/b/t/{_PREFIJO_BORRANDO}2026-01-01_000000")


def test_comando_retencion_borra_descolgado_y_conserva_el_resto(tmp_path):
    raiz = tmp_path / "carpeta con espacios" / "incremental"
    for nombre in ("2026-01-01_000000", "2026-01-02_000000", "2026-01-09_000000"):
        (raiz / nombre / "sub").mkdir(parents=True)
        (raiz / nombre / "sub" / "f.txt").write_text("x")
    (raiz / f"{_PREFIJO_BORRANDO}resto_interrumpido" / "a").mkdir(parents=True)
    (raiz / "current").symlink_to("2026-01-09_000000")

    caducados = [str(raiz / "2026-01-01_000000"), str(raiz / "2026-01-02_000000")]
    t0 = time.monotonic()
    subprocess.run(["sh", "-c", comando_retencion(str(raiz), caducados)], check=True, timeout=10)
    assert time.monotonic() - t0 < 5   # vuelve enseguida: el borrado va en segundo plano

    # Lo caducado deja de verse al momento (renombrado)…
    assert not (raiz / "2026-01-01_000000").exists()
    assert not (raiz / "2026-01-02_000000").exists()
    # …y el borrado en segundo plano termina con ello y con los restos anteriores.
    limite = time.monotonic() + 10
    while time.monotonic() < limite and any(p.name.startswith(_PREFIJO_BORRANDO) for p in raiz.iterdir()):
        time.sleep(0.1)
    assert sorted(p.name for p in raiz.iterdir()) == ["2026-01-09_000000", "current"]
    assert (raiz / "current" / "sub" / "f.txt").read_text() == "x"


def test_comando_retencion_sin_caducados_solo_limpia_restos(tmp_path):
    cmd = comando_retencion(str(tmp_path), [])
    assert "mv " not in cmd and "nohup sh -c" in cmd


# --- Monitor y analizador -----------------------------------------------------

def _destino_simple():
    with session_scope() as s:
        d = Destino(nombre="mac", host="10.0.0.9", usuario="bk", auth_method="password",
                    secret_cifrado="y", carpeta_base="/Volumes/RAID", espacio_total=1,
                    espacio_libre=1, espacio_backups=7)
        s.add(d)
        s.flush()
        return d.id


def _box():
    from app.crypto import SecretBox, generate_key
    return SecretBox(generate_key())


def test_monitor_df_ilegible_no_marca_inaccesible(client, monkeypatch):
    import daemon.monitor as monitor

    did = _destino_simple()
    monkeypatch.setattr(monitor, "_target_destino", lambda d, box: None)
    monkeypatch.setattr(monitor, "disk_space", lambda target, path: None)
    monitor.check_destinos(_box())
    with session_scope() as s:
        d = s.get(Destino, did)
        assert d.estado == "conectado" and d.espacio_total == 1   # se conserva la medida previa


def test_monitor_sin_conexion_marca_inaccesible(client, monkeypatch):
    import daemon.monitor as monitor

    did = _destino_simple()

    def sin_conexion(target, path):
        raise SshError("timed out")

    monkeypatch.setattr(monitor, "_target_destino", lambda d, box: None)
    monkeypatch.setattr(monitor, "disk_space", sin_conexion)
    monitor.check_destinos(_box())
    with session_scope() as s:
        assert s.get(Destino, did).estado == "inaccesible"


def test_monitor_no_mide_las_copias_y_el_analizador_si(client, monkeypatch):
    import daemon.monitor as monitor

    did = _destino_simple()
    monkeypatch.setattr(monitor, "_target_destino", lambda d, box: None)
    monkeypatch.setattr(monitor, "disk_space", lambda target, path: (2048, 1024))
    llamadas = []
    monkeypatch.setattr(monitor, "backups_size", lambda target, path: llamadas.append(path) or 4096)
    monitor.check_destinos(_box())
    assert llamadas == []                                   # el monitor ya no lanza du
    monitor.medir_backups_destinos(_box())
    assert llamadas == ["/Volumes/RAID"]
    with session_scope() as s:
        d = s.get(Destino, did)
        assert (d.espacio_total, d.espacio_libre, d.espacio_backups) == (2048, 1024, 4096)


def test_du_que_falla_conserva_la_medida_y_el_estado(client, monkeypatch):
    import daemon.monitor as monitor

    did = _destino_simple()

    def timeout(target, path):
        raise SshError("timed out")

    monkeypatch.setattr(monitor, "_target_destino", lambda d, box: None)
    monkeypatch.setattr(monitor, "backups_size", timeout)
    monitor.medir_backups_destinos(_box())
    with session_scope() as s:
        d = s.get(Destino, did)
        assert d.espacio_backups == 7 and d.estado == "desconocido"


# --- Web: opciones de copia del destino y ajuste de la cola --------------------

def _alta_destino(auth_client, **extra):
    data = {"nombre": "mac", "host": "10.0.0.9", "puerto": 22, "usuario": "bk",
            "auth_method": "password", "secret": "s", "carpeta_base": "/Volumes/RAID",
            "proteccion": "raid1"}
    data.update(extra)
    return auth_client.post("/destinos", data=data, follow_redirects=False)


def test_alta_destino_con_opciones_de_copia(auth_client):
    r = _alta_destino(auth_client, max_copias=2, rsync_remoto="/usr/local/bin/rsync")
    assert r.status_code == 303
    with session_scope() as s:
        d = s.query(Destino).one()
        assert d.max_copias == 2 and d.rsync_remoto == "/usr/local/bin/rsync"


def test_alta_destino_por_defecto_una_copia(auth_client):
    _alta_destino(auth_client)
    with session_scope() as s:
        d = s.query(Destino).one()
        assert d.max_copias == 1 and d.rsync_remoto is None


def test_alta_destino_rechaza_rsync_remoto_peligroso(auth_client):
    r = _alta_destino(auth_client, rsync_remoto="/usr/bin/rsync; rm -rf /")
    assert r.status_code == 200 and "debe ser absoluta" in r.text
    with session_scope() as s:
        assert s.query(Destino).count() == 0


def test_editar_destino(auth_client):
    _alta_destino(auth_client)
    with session_scope() as s:
        did = s.query(Destino).one().id
    assert "Editar destino" in auth_client.get(f"/destinos/{did}/editar").text
    r = auth_client.post(f"/destinos/{did}/editar", data={
        "proteccion": "raid2", "ubicacion_id": "", "max_copias": 99,
        "rsync_remoto": "/usr/local/bin/rsync"}, follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        d = s.get(Destino, did)
        assert d.max_copias == 16 and d.rsync_remoto == "/usr/local/bin/rsync"   # acotado
        assert d.proteccion == "raid2" and d.carpeta_base == "/Volumes/RAID"     # intacta
    html = auth_client.get("/destinos").text
    assert "/usr/local/bin/rsync" in html and f"/destinos/{did}/editar" in html


def test_editar_destino_invalido_no_guarda(auth_client):
    _alta_destino(auth_client)
    with session_scope() as s:
        did = s.query(Destino).one().id
    r = auth_client.post(f"/destinos/{did}/editar", data={
        "proteccion": "raid1", "ubicacion_id": "", "max_copias": 1,
        "rsync_remoto": "rsync"}, follow_redirects=False)
    assert r.status_code == 200 and "debe ser absoluta" in r.text
    with session_scope() as s:
        assert s.get(Destino, did).rsync_remoto is None


def test_ajuste_copias_por_origen(auth_client):
    from app.settings import max_copias_por_origen

    with session_scope() as s:
        assert max_copias_por_origen(s) == 1
    r = auth_client.post("/ajustes/cola", data={"max_copias_por_origen": 0}, follow_redirects=False)
    assert r.status_code == 303 and "cola-guardada" in r.headers["location"]
    with session_scope() as s:
        assert max_copias_por_origen(s) == 1          # mínimo 1
    auth_client.post("/ajustes/cola", data={"max_copias_por_origen": 2}, follow_redirects=False)
    with session_scope() as s:
        assert max_copias_por_origen(s) == 2
    assert "Copias simultáneas por host de origen" in auth_client.get("/ajustes").text


def test_formulario_alta_muestra_opciones_de_copia(auth_client):
    html = auth_client.get("/destinos/nuevo").text
    assert 'name="max_copias"' in html and 'value="1"' in html
    assert 'name="rsync_remoto"' in html and "openrsync" in html
