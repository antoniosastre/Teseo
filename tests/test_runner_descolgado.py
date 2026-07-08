"""Tests de la ejecución descolgada de copias (daemon/runner.py)."""
from __future__ import annotations

from daemon.runner import _parse_estado, _rutas, _script_copia
from app.rsync_cmd import ssh_transport


def test_rutas():
    r = _rutas(7)
    assert r["sh"].endswith("/task_7.sh")
    assert r["rc"].endswith("/task_7.rc")
    assert r["pid"].endswith("/task_7.pid")
    assert r["log"].endswith("/task_7.log")


def test_script_copia_registra_pid_y_rc():
    script = _script_copia("rsync -a /a/ u@h:/b/", 3)
    assert script.startswith("#!/bin/sh")
    assert "rsync -a /a/ u@h:/b/" in script
    assert "echo $$ >" in script            # registra su PID
    assert "echo $? >" in script            # registra el código de salida al terminar
    assert "task_3.rc" in script and "task_3.log" in script


def test_parse_estado_en_curso():
    out = "RC=\nALIVE=yes\n===LOG===\n   1,234  42%   2.7MB/s\n"
    rc, alive, log = _parse_estado(out)
    assert rc is None and alive is True and "42%" in log


def test_parse_velocidad_del_log():
    from daemon.runner import _VEL_RE

    # Líneas reales de --info=progress2 (separadas por \r en el log).
    log = ("  1,262,293,084  99%    4.72MB/s    0:04:14 (xfr#23682, to-chk=44/25608)\r"
           "  1,262,301,276  99%    5.03MB/s    0:04:15 (xfr#23683, to-chk=40/25608)")
    vels = _VEL_RE.findall(log)
    assert vels[-1] == "5.03MB/s"           # la última es la vigente
    assert _VEL_RE.findall("  9,876  3%  612.34kB/s  0:00:02")[-1] == "612.34kB/s"
    assert _VEL_RE.findall("sin progreso todavia") == []


def test_parse_estado_terminada_ok():
    out = "RC=0\nALIVE=no\n===LOG===\nsent 1,234 bytes  received 56 bytes\n"
    rc, alive, log = _parse_estado(out)
    assert rc == 0 and alive is False and "sent 1,234 bytes" in log


def test_parse_estado_interrumpida():
    # Sin .rc y sin proceso vivo -> candidata a interrumpida.
    rc, alive, log = _parse_estado("RC=\nALIVE=no\n===LOG===\n")
    assert rc is None and alive is False and log == ""


def test_parse_estado_fallo_con_codigo():
    rc, alive, _ = _parse_estado("RC=23\nALIVE=no\n===LOG===\nrsync error ...\n")
    assert rc == 23 and alive is False


def test_ssh_transport_endurecido():
    # Sin clave: BatchMode siempre.
    t = ssh_transport(22, None)
    assert "-o BatchMode=yes" in t and "IdentitiesOnly" not in t
    # Con clave: BatchMode + IdentitiesOnly + la clave.
    t = ssh_transport(2222, "/home/u/.ssh/k")
    assert "-p 2222" in t and "-i /home/u/.ssh/k" in t
    assert "-o BatchMode=yes" in t and "-o IdentitiesOnly=yes" in t
