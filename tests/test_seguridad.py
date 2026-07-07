"""Tests de las correcciones de seguridad (inyección, pinning SSH, login, DDL)."""
from __future__ import annotations

import shlex

import paramiko
import pytest

from app.rsync_cmd import build_plan, validate_override


# --- #1 Inyección de comandos ------------------------------------------------

def test_rsync_extra_se_cita_y_neutraliza_inyeccion():
    """Un rsync_extra malicioso queda citado como un único token inerte."""
    p = build_plan(
        carpeta_origen="/data", carpeta_base="/bk", host_nombre="h", tipo="espejo",
        destino_usuario="u", destino_host="nas", key_path="/k",
        extra_flags="--rsh=$(reboot)",
    )
    # La subshell no debe quedar 'desnuda' en el comando: va entre comillas.
    assert "$(reboot)" not in p.command.replace("'--rsh=$(reboot)'", "")
    # Y al re-parsear el comando, la sustitución vive dentro de un solo token.
    assert "--rsh=$(reboot)" in shlex.split(p.command)


def test_validate_override_acepta_rsync_simple():
    assert validate_override("rsync -avz /a/ u@h:/b/") is None
    assert validate_override("") is None
    assert validate_override("   ") is None


def test_validate_override_rechaza_no_rsync_y_metacaracteres():
    assert validate_override("rm -rf /") is not None
    assert validate_override("rsync -a /a/ u@h:/b/; rm -rf /") is not None
    assert validate_override("rsync -a /a/ u@h:/b/ && curl evil") is not None
    assert validate_override("rsync -a $(whoami)") is not None
    assert validate_override("rsync -a /a/ > /etc/passwd") is not None


def test_override_rechazado_en_alta_de_tarea(auth_client):
    from app.db import session_scope
    from app.models import Destino, HostOrigen

    auth_client.post("/destinos", data={
        "nombre": "nas1", "host": "10.0.0.9", "puerto": 22, "usuario": "bk",
        "auth_method": "password", "secret": "s", "carpeta_base": "/backups",
        "proteccion": "raid2"}, follow_redirects=False)
    auth_client.post("/origenes/host", data={
        "nombre": "web1", "host": "10.0.0.5", "puerto": 22, "usuario": "root",
        "auth_method": "password", "secret": "pw", "es_raid": "raid1"},
        follow_redirects=False)
    with session_scope() as s:
        hid = s.query(HostOrigen).first().id
        did = s.query(Destino).first().id

    bad = auth_client.post("/origenes", data={
        "host_id": hid, "destino_id": did, "carpeta_origen": "/var/www",
        "tipo": "espejo", "cron": "0 2 * * *", "retencion": 3,
        "comando_rsync": "rsync -a /a/ u@h:/b/; rm -rf /"}, follow_redirects=False)
    assert bad.status_code == 200 and "rsync" in bad.text.lower()


# --- #2 Pinning de clave de host SSH -----------------------------------------

def _fake_key(seed: bytes) -> paramiko.PKey:
    return paramiko.Ed25519Key.from_private_key(_ed25519_pem(seed))


def _ed25519_pem(seed: bytes):
    import io

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(seed)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return io.StringIO(pem)


def test_pinned_policy_detecta_cambio_de_clave():
    from app.remote import SshError, _hostkey_repr, _PinnedPolicy

    esperada = _fake_key(b"\x01" * 32)
    otra = _fake_key(b"\x02" * 32)

    # Clave que coincide: no lanza.
    policy = _PinnedPolicy(_hostkey_repr(esperada))
    policy.missing_host_key(None, "nas", esperada)

    # Clave distinta: aborta por posible MITM.
    policy = _PinnedPolicy(_hostkey_repr(esperada))
    with pytest.raises(SshError):
        policy.missing_host_key(None, "nas", otra)


def test_pinned_policy_aprende_en_primer_uso():
    from app.remote import _hostkey_repr, _PinnedPolicy

    k = _fake_key(b"\x03" * 32)
    policy = _PinnedPolicy(None)  # sin clave esperada -> TOFU
    policy.missing_host_key(None, "nas", k)
    assert policy.learned == _hostkey_repr(k)


# --- #4 Login: freno de fuerza bruta y timing --------------------------------

def test_login_se_bloquea_tras_varios_fallos(client):
    import app.auth as auth

    auth._failures.clear()
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "mal"},
                    follow_redirects=False)
    r = client.post("/login", data={"username": "admin", "password": "contrasena1"},
                    follow_redirects=False)
    # Aunque la contraseña sea correcta, queda bloqueado por IP.
    assert r.status_code == 429
    auth._failures.clear()


def test_authenticate_no_revela_si_usuario_existe():
    from app.auth import authenticate
    # Usuario inexistente devuelve None sin excepción (verificación señuelo).
    assert authenticate("no-existe", "loquesea") is None


# --- #6 Inyección SQL en nombre de BD ----------------------------------------

def test_create_database_valida_nombre():
    from app.config import DatabaseConfig
    from app.installer.service import create_database

    db = DatabaseConfig(host="h", port=3306, user="u", password="p",
                        name="teseo`; DROP DATABASE otra; --")
    with pytest.raises(ValueError):
        create_database(db)
