"""Tests de integración de la web (sobre SQLite en memoria)."""
from __future__ import annotations

from app.db import session_scope
from app.models import Destino, HostOrigen, Tarea


def _crear_entorno(client):
    ub_a = client.post("/ubicaciones", data={"nombre": "CPD-A"}).json()["id"]
    ub_b = client.post("/ubicaciones", data={"nombre": "CPD-B"}).json()["id"]
    client.post("/destinos", data={
        "nombre": "nas1", "host": "10.0.0.9", "puerto": 22, "usuario": "bk",
        "auth_method": "password", "secret": "secreto", "carpeta_base": "/backups",
        "proteccion": "raid2", "ubicacion_id": str(ub_b)}, follow_redirects=False)
    client.post("/origenes/host", data={
        "nombre": "web1", "host": "10.0.0.5", "puerto": 22, "usuario": "root",
        "auth_method": "password", "secret": "pw", "es_raid": "raid1",
        "ubicacion_id": str(ub_a)}, follow_redirects=False)
    with session_scope() as s:
        return s.query(HostOrigen).first().id, s.query(Destino).first().id


def test_login_required(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_login_invalido(client):
    r = client.post("/login", data={"username": "admin", "password": "mal"}, follow_redirects=False)
    assert r.status_code == 200 and "incorrect" in r.text.lower()


def test_secreto_se_cifra(auth_client):
    _crear_entorno(auth_client)
    with session_scope() as s:
        h = s.query(HostOrigen).first()
        assert h.secret_cifrado and h.secret_cifrado != "pw"


def test_preview_comando(auth_client):
    hid, did = _crear_entorno(auth_client)
    r = auth_client.post("/origenes/preview", data={
        "host_id": hid, "destino_id": did, "carpeta_origen": "/var/www",
        "tipo": "incremental", "rsync_extra": "--exclude=*.tmp"})
    cmd = r.json()["command"]
    assert cmd.startswith("rsync") and "--link-dest=../current" in cmd and "--exclude=*.tmp" in cmd


def test_crear_tarea_y_cron_invalido(auth_client):
    hid, did = _crear_entorno(auth_client)
    ok = auth_client.post("/origenes", data={
        "host_id": hid, "destino_id": did, "carpeta_origen": "/var/www",
        "tipo": "incremental", "cron": "0 2 * * *", "retencion": 5}, follow_redirects=False)
    assert ok.status_code == 303
    bad = auth_client.post("/origenes", data={
        "host_id": hid, "destino_id": did, "carpeta_origen": "/srv",
        "tipo": "espejo", "cron": "no-valido", "retencion": 3}, follow_redirects=False)
    assert bad.status_code == 200 and "cron" in bad.text.lower()


def test_scoring(auth_client):
    hid, did = _crear_entorno(auth_client)
    auth_client.post("/origenes", data={
        "host_id": hid, "destino_id": did, "carpeta_origen": "/var/www",
        "tipo": "incremental", "cron": "0 2 * * *", "retencion": 5}, follow_redirects=False)
    from app.services import tarea_score
    with session_scope() as s:
        t = s.query(Tarea).first()
        pts, clase = tarea_score(t)
    # raid1 (1) + raid2 (2) + ubicaciones distintas (2) = 5 -> excelente
    assert pts == 5 and clase == "excelente"


def test_run_now_y_estado(auth_client):
    hid, did = _crear_entorno(auth_client)
    auth_client.post("/origenes", data={
        "host_id": hid, "destino_id": did, "carpeta_origen": "/var/www",
        "tipo": "espejo", "cron": "0 2 * * *", "retencion": 5}, follow_redirects=False)
    with session_scope() as s:
        tid = s.query(Tarea).first().id
    r = auth_client.post(f"/tareas/{tid}/run", follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        assert s.query(Tarea).first().run_now is True
    j = auth_client.get("/estado/json").json()
    assert str(tid) in j["tareas"]
