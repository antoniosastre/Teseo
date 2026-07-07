"""Tests unitarios de construcción de comando rsync, scoring y cifrado."""
from __future__ import annotations

from app.crypto import SecretBox, generate_key
from app.rsync_cmd import build_plan, dest_task_dir, sanitize_component
from scoring import (
    MAX_SCORE,
    CopiaInputs,
    ScoreInputs,
    classify,
    origen_score,
    score,
    score_bar,
)


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


def test_scoring_maximo_son_6_puntos():
    # raid2 origen (2) + copia (1) + raid2 destino (2) + ubicación distinta (1) = 6
    assert score(ScoreInputs("raid2", "raid2", 1, 2)) == 6 == MAX_SCORE


def test_scoring_componentes():
    # single/single, misma ubicación, con copia -> solo el +1 de "tiene copia".
    assert score(ScoreInputs("single", "single", 1, 1)) == 1
    # sin copia de seguridad -> 0.
    assert score(ScoreInputs("single", "single", 1, 1, tiene_copia=False)) == 0
    # raid1 origen (1) + copia (1) + destino single (0) + ubicación distinta (1) = 3
    assert score(ScoreInputs("raid1", "single", 1, 2)) == 3
    # ubicación desconocida (None) no puntúa aunque difieran conceptualmente.
    assert score(ScoreInputs("single", "single", None, 2)) == 1


def test_classify_labels():
    assert classify(0) == "mínima"
    assert classify(2) == "básica"
    assert classify(4) == "buena"
    assert classify(6) == "excelente"


def test_origen_score_sin_copias():
    # raid1 en el volumen, sin ninguna tarea -> solo el +1 del RAID origen.
    assert origen_score("raid1", []) == 1
    assert origen_score("single", []) == 0


def test_origen_score_mejor_copia():
    # Origen raid2 (2) + tiene copia (1) + mejor destino de entre sus tareas.
    copias = [
        CopiaInputs(destino_proteccion="single", ubicacion_distinta=False),   # destino: 0
        CopiaInputs(destino_proteccion="raid2", ubicacion_distinta=True),     # destino: 2+1=3 (mejor)
    ]
    # 2 (raid2 origen) + 1 (tiene copia) + 3 (mejor copia) = 6
    assert origen_score("raid2", copias) == 6 == MAX_SCORE


def test_origen_score_una_copia_local():
    # single origen (0) + copia (1) + destino raid1 (1) misma ubicación (0) = 2
    assert origen_score("single", [CopiaInputs("raid1", False)]) == 2


def test_score_bar_mapeo_usuario():
    esperado = {
        0: (10, "rojo"), 1: (20, "naranja"), 2: (40, "amarillo"),
        3: (60, "verde"), 4: (80, "verde"), 5: (90, "azul"), 6: (100, "azul"),
    }
    for pts, (pct, color) in esperado.items():
        bar = score_bar(pts)
        assert bar.pct == pct and bar.color == color and bar.puntos == pts
    # Fuera de rango se recorta a [0, MAX_SCORE].
    assert score_bar(99).pct == 100 and score_bar(-3).pct == 10


def test_cifrado_roundtrip():
    box = SecretBox(generate_key())
    token = box.encrypt("secreto")
    assert token != "secreto"
    assert box.decrypt(token) == "secreto"
    assert box.encrypt("") is None and box.decrypt(None) is None
