"""Tests unitarios de construcción de comando rsync, scoring y cifrado."""
from __future__ import annotations

from app.crypto import SecretBox, generate_key
from app.rsync_cmd import build_plan, dest_task_dir, sanitize_component
from scoring import ScoreInputs, classify, score


def test_sanitize_component():
    assert sanitize_component("/var/www/") == "var_www"
    assert sanitize_component("a b#c") == "a_b_c"
    assert sanitize_component("/") == "root"


def test_dest_layout():
    d = dest_task_dir("/backups/", "web 1", "incremental", "/var/www")
    assert d == "/backups/web_1/incremental/var_www"


def test_espejo_lleva_delete():
    p = build_plan(carpeta_origen="/data", carpeta_base="/bk", host_nombre="h",
                   tipo="espejo", destino_usuario="u", destino_host="nas", key_path="/k")
    assert "--delete" in p.command and p.dest_target.endswith("/current")
    assert p.snapshot_name is None


def test_incremental_usa_link_dest():
    p = build_plan(carpeta_origen="/data", carpeta_base="/bk", host_nombre="h",
                   tipo="incremental", destino_usuario="u", destino_host="nas", key_path="/k")
    assert "--link-dest=../current" in p.command
    assert p.snapshot_name and p.snapshot_name in p.dest_target


def test_transporte_ssh_con_puerto_y_clave():
    p = build_plan(carpeta_origen="/data", carpeta_base="/bk", host_nombre="h",
                   tipo="espejo", destino_usuario="u", destino_host="nas",
                   destino_puerto=2222, key_path="/home/u/.ssh/k")
    assert "-p 2222" in p.command and "-i /home/u/.ssh/k" in p.command


def test_scoring_niveles():
    assert score(ScoreInputs("raid2", "raid2", 1, 2)) == 6
    assert score(ScoreInputs("single", "single", 1, 1)) == 0
    assert score(ScoreInputs("raid1", "single", 1, 2)) == 3
    assert classify(0) == "mínima" and classify(6) == "excelente"


def test_cifrado_roundtrip():
    box = SecretBox(generate_key())
    token = box.encrypt("secreto")
    assert token != "secreto"
    assert box.decrypt(token) == "secreto"
    assert box.encrypt("") is None and box.decrypt(None) is None
