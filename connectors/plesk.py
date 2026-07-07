"""Conector para servidores Plesk (Linux).

Metodología de descubrimiento:
  1. Lee /etc/psa/psa.conf para resolver las rutas base (HTTPD_VHOSTS_D,
     PLESK_MAILNAMES_D, DUMP_D), con valores por defecto si faltan.
  2. Construye la lista de orígenes candidatos (ver más abajo) según las
     opciones elegidas por el usuario.
  3. Calcula el punto de montaje de cada origen (``df``) y los agrupa: cada
     punto de montaje distinto es un "volumen" al que se le asigna protección.

Orígenes:
  - "Configuración Plesk"  -> /etc/psa (incluye psa.conf)
  - "Configuración vhosts" -> <vhosts>/system (confs por dominio)
  - un origen por cada suscripción (``plesk bin subscription --list``)
  - "Correo (mailnames)"   -> <mailnames>            (si se marca Copiar Emails)
  - "Dumps MySQL"/"Dumps PostgreSQL" -> /var/mysqldumps y /var/pg_dumps si existen
                                        (si se marca Copiar Bases de Datos)
  - "Backups Plesk (DUMP_D)" -> <dump_d>             (si se marca Copiar Backups)
  - un origen por cada ruta adicional indicada que exista

Nunca se copian ficheros vivos de BD (/var/lib/mysql): el usuario genera los
dumps en /var/mysqldumps y /var/pg_dumps externamente y Teseo los respalda.
"""
from __future__ import annotations

import shlex

from connectors import Ejecutar, OpcionDescubrimiento, OrigenDescubierto, VolumenDescubierto

_DEFAULTS = {
    "HTTPD_VHOSTS_D": "/var/www/vhosts",
    "PLESK_MAILNAMES_D": "/var/qmail/mailnames",
    "DUMP_D": "/var/lib/psa/dumps",
}


def _flag(opciones: dict, clave: str) -> bool:
    return str(opciones.get(clave, "")).lower() in ("1", "true", "on", "yes", "si", "sí")


def _lineas(texto: str) -> list[str]:
    return [ln.strip() for ln in (texto or "").splitlines() if ln.strip()]


def _basename(ruta: str) -> str:
    limpia = ruta.rstrip("/")
    return limpia.rsplit("/", 1)[-1] if "/" in limpia else limpia or ruta


class PleskLinuxConnector:
    TIPO = "plesk_linux"
    NOMBRE = "Plesk (Linux)"

    def opciones_descubrimiento(self) -> list[OpcionDescubrimiento]:
        return [
            OpcionDescubrimiento("copiar_emails", "Copiar Emails", "checkbox"),
            OpcionDescubrimiento("copiar_bd", "Copiar Bases de Datos", "checkbox"),
            OpcionDescubrimiento("copiar_backups", "Copiar Backups", "checkbox"),
            OpcionDescubrimiento(
                "rutas_extra", "Rutas adicionales (una por línea)", "textarea"
            ),
        ]

    # --- helpers de shell (inyectables en tests) -----------------------------

    def _leer_psa_conf(self, ejecutar: Ejecutar) -> dict[str, str]:
        rc, out, _ = ejecutar("cat /etc/psa/psa.conf 2>/dev/null")
        conf = dict(_DEFAULTS)
        if rc == 0:
            for linea in out.splitlines():
                linea = linea.strip()
                if not linea or linea.startswith("#"):
                    continue
                partes = linea.split(None, 1)
                if len(partes) == 2:
                    conf[partes[0]] = partes[1].strip()
        return conf

    def _subscripciones(self, ejecutar: Ejecutar) -> list[str]:
        rc, out, _ = ejecutar("plesk bin subscription --list 2>/dev/null")
        if rc != 0:
            return []
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def _existe(self, ejecutar: Ejecutar, ruta: str) -> bool:
        rc, out, _ = ejecutar(f"test -e {shlex.quote(ruta)} && echo ok")
        return rc == 0 and "ok" in out

    def _mount(self, ejecutar: Ejecutar, ruta: str) -> tuple[str, str] | None:
        """Devuelve (dispositivo, punto_de_montaje) de la ruta, o None si falla."""
        rc, out, _ = ejecutar(f"df -P {shlex.quote(ruta)} 2>/dev/null | tail -1")
        if rc != 0:
            return None
        partes = out.split()
        if len(partes) < 6:
            return None
        return partes[0], partes[5]  # Filesystem, Mounted-on

    # --- descubrimiento ------------------------------------------------------

    def descubrir(self, ejecutar: Ejecutar, opciones: dict | None = None) -> list[VolumenDescubierto]:
        opciones = opciones or {}
        conf = self._leer_psa_conf(ejecutar)
        vhosts = conf["HTTPD_VHOSTS_D"].rstrip("/")
        mailnames = conf["PLESK_MAILNAMES_D"]
        dump_d = conf["DUMP_D"]

        candidatos: list[tuple[str, str]] = [
            ("Configuración Plesk", "/etc/psa"),
            ("Configuración vhosts", f"{vhosts}/system"),
        ]
        for dominio in self._subscripciones(ejecutar):
            candidatos.append((dominio, f"{vhosts}/{dominio}"))
        if _flag(opciones, "copiar_emails"):
            candidatos.append(("Correo (mailnames)", mailnames))
        if _flag(opciones, "copiar_bd"):
            for nombre, ruta in (("Dumps MySQL", "/var/mysqldumps"),
                                 ("Dumps PostgreSQL", "/var/pg_dumps")):
                if self._existe(ejecutar, ruta):
                    candidatos.append((nombre, ruta))
        if _flag(opciones, "copiar_backups"):
            candidatos.append(("Backups Plesk (DUMP_D)", dump_d))
        for linea in _lineas(opciones.get("rutas_extra", "")):
            if self._existe(ejecutar, linea):
                candidatos.append((_basename(linea), linea))

        # Agrupar por punto de montaje (cada uno es un "volumen" con su protección).
        grupos: dict[str, tuple[str, list[OrigenDescubierto]]] = {}
        for nombre, ruta in candidatos:
            m = self._mount(ejecutar, ruta)
            if m is None:
                continue
            dispositivo, punto = m
            dev, origenes = grupos.setdefault(punto, (dispositivo, []))
            origenes.append(OrigenDescubierto(nombre=nombre, tipo="carpeta", ruta=ruta))

        return [
            VolumenDescubierto(nombre=punto, dispositivo=dev, origenes=origenes)
            for punto, (dev, origenes) in grupos.items()
        ]

    def fuente_rsync(self, tipo_origen: str, ruta: str) -> tuple[str, list[str]]:
        return ruta, []

    def medir_tamano(self, ejecutar: Ejecutar, tipo_origen: str, ruta: str) -> int | None:
        rc, out, _ = ejecutar(f"du -sb {shlex.quote(ruta)} 2>/dev/null | cut -f1")
        out = out.strip()
        return int(out) if rc == 0 and out.isdigit() else None
