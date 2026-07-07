"""Tests del conector Plesk (Linux) con un ejecutor de comandos falso."""
from __future__ import annotations

import shlex

from connectors.plesk import PleskLinuxConnector

_PSA_CONF = (
    "# psa.conf\n"
    "HTTPD_VHOSTS_D /var/www/vhosts\n"
    "PLESK_MAILNAMES_D /var/qmail/mailnames\n"
    "DUMP_D /var/lib/psa/dumps\n"
)


def _mount_de(ruta, mounts):
    """Punto de montaje más específico (prefijo más largo) que contiene la ruta."""
    mejor = None
    for mp in mounts:
        if ruta == mp or ruta.startswith(mp.rstrip("/") + "/") or mp == "/":
            if mejor is None or len(mp) > len(mejor):
                mejor = mp
    return mejor


def _fake_plesk(subs, existentes, mounts, psa_conf=_PSA_CONF):
    def ejecutar(cmd):
        if cmd.startswith("cat /etc/psa/psa.conf"):
            return (0, psa_conf, "") if psa_conf is not None else (1, "", "")
        if cmd.startswith("plesk bin subscription --list"):
            return (0, "\n".join(subs) + "\n", "")
        if cmd.startswith("test -e "):
            ruta = shlex.split(cmd)[2]
            return (0, "ok\n", "") if ruta in existentes else (1, "", "")
        if cmd.startswith("df -P "):
            ruta = shlex.split(cmd.split("2>")[0])[2]
            mp = _mount_de(ruta, mounts)
            if mp is None:
                return (1, "", "")
            return (0, f"{mounts[mp]} 100 50 50 50% {mp}\n", "")
        if cmd.startswith("du -sb "):
            return (0, "4096\n", "")
        return (127, "", "comando no simulado")

    return ejecutar


# titanio-like: / en /dev/md3, /var/www en /dev/md4
_MOUNTS = {"/": "/dev/md3", "/var/www": "/dev/md4"}


def test_opciones_descubrimiento():
    claves = [o.clave for o in PleskLinuxConnector().opciones_descubrimiento()]
    assert claves == ["copiar_emails", "copiar_bd", "copiar_backups", "rutas_extra"]


def test_descubrir_agrupa_por_mountpoint():
    ejecutar = _fake_plesk(["example.com", "foo.online"], {"/var/mysqldumps"}, _MOUNTS)
    vols = {v.nombre: v for v in PleskLinuxConnector().descubrir(ejecutar, {})}
    assert set(vols) == {"/", "/var/www"}
    assert vols["/"].dispositivo == "/dev/md3"
    assert vols["/var/www"].dispositivo == "/dev/md4"
    # /etc/psa cae en "/"; vhosts/system y las suscripciones en "/var/www".
    raiz = {o.nombre for o in vols["/"].origenes}
    www = {o.nombre for o in vols["/var/www"].origenes}
    assert "Configuración Plesk" in raiz
    assert {"Configuración vhosts", "example.com", "foo.online"} <= www


def test_flags_controlan_origenes_opcionales():
    subs = ["example.com"]
    existentes = {"/var/mysqldumps"}  # pg_dumps NO existe
    # Sin flags: no correo, no dumps, no backups.
    vols = PleskLinuxConnector().descubrir(_fake_plesk(subs, existentes, _MOUNTS), {})
    nombres = {o.nombre for v in vols for o in v.origenes}
    assert "Correo (mailnames)" not in nombres
    assert "Dumps MySQL" not in nombres and "Backups Plesk (DUMP_D)" not in nombres
    # Con los tres flags: correo, dumps (solo MySQL, pg no existe) y backups.
    op = {"copiar_emails": True, "copiar_bd": True, "copiar_backups": True}
    vols = PleskLinuxConnector().descubrir(_fake_plesk(subs, existentes, _MOUNTS), op)
    nombres = {o.nombre for v in vols for o in v.origenes}
    assert "Correo (mailnames)" in nombres
    assert "Dumps MySQL" in nombres
    assert "Dumps PostgreSQL" not in nombres      # no existe -> no se añade
    assert "Backups Plesk (DUMP_D)" in nombres


def test_rutas_extra_existentes_se_anaden_y_faltantes_no():
    op = {"rutas_extra": "/srv/datos\n/no/existe"}
    ejecutar = _fake_plesk(["example.com"], {"/srv/datos"}, {"/": "/dev/md3"})
    vols = PleskLinuxConnector().descubrir(ejecutar, op)
    nombres = {o.nombre for v in vols for o in v.origenes}
    assert "datos" in nombres            # basename de /srv/datos
    assert all(o.ruta != "/no/existe" for v in vols for o in v.origenes)


def test_psa_conf_ausente_usa_defaults():
    # cat falla -> se usan las rutas por defecto y aun así descubre config/vhosts.
    ejecutar = _fake_plesk([], set(), _MOUNTS, psa_conf=None)
    vols = PleskLinuxConnector().descubrir(ejecutar, {})
    rutas = {o.ruta for v in vols for o in v.origenes}
    assert "/etc/psa" in rutas and "/var/www/vhosts/system" in rutas


def test_medir_tamano():
    ejecutar = _fake_plesk([], set(), _MOUNTS)
    assert PleskLinuxConnector().medir_tamano(ejecutar, "carpeta", "/etc/psa") == 4096
