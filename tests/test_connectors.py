"""Tests del framework de conectores y del conector Synology.

El descubrimiento se prueba con un ejecutor de comandos falso (sin SSH real).
"""
from __future__ import annotations

import pytest

from connectors import get_connector, conectores_disponibles
from connectors.synology import SynologyConnector, _origenes_de_volumen, _parse_ls_ap, _Entrada


def _fake_synology(fs: dict[str, list[str]]):
    """Crea un ejecutor falso a partir de un 'sistema de ficheros' simulado.

    ``fs`` mapea ruta de volumen -> lista de entradas de ``ls -1Ap`` (dirs con '/').
    """
    def ejecutar(cmd: str):
        if cmd.startswith("test -d "):
            ruta = cmd.split("test -d ", 1)[1].split(" &&")[0].strip("'\"")
            return (0, "ok\n", "") if ruta in fs else (1, "", "")
        if cmd.startswith("ls -1Ap "):
            ruta = cmd.split("ls -1Ap ", 1)[1].strip().strip("'\"")
            return (0, "\n".join(fs.get(ruta, [])) + "\n", "")
        return (127, "", "comando no simulado")

    return ejecutar


# --- Registro ----------------------------------------------------------------

def test_registro_incluye_synology_y_plesk():
    tipos = dict(conectores_disponibles())
    assert tipos["synology"] == "NAS Synology"
    assert tipos["plesk_linux"] == "Plesk (Linux)"


def test_get_connector_desconocido():
    with pytest.raises(ValueError):
        get_connector("noexiste")


# --- Parseo de ls -1Ap -------------------------------------------------------

def test_parse_ls_ap_distingue_dirs():
    entradas = _parse_ls_ap("@appstore/\n@database/\nweb/\ndatos/\nreadme.txt\n")
    porn = {e.nombre: e.es_dir for e in entradas}
    assert porn == {"@appstore": True, "@database": True, "web": True, "datos": True, "readme.txt": False}


# --- Reglas de orígenes por volumen ------------------------------------------

def test_origenes_bundle_configuracion_y_carpetas():
    entradas = [
        _Entrada("@appstore", True), _Entrada("@database", True),
        _Entrada("web", True), _Entrada("documentos", True), _Entrada("suelto.txt", False),
    ]
    origenes = _origenes_de_volumen("/volume1", entradas)
    nombres = [(o.nombre, o.tipo, o.ruta) for o in origenes]
    assert ("Configuración", "config", "/volume1") in nombres
    assert ("web", "carpeta", "/volume1/web") in nombres
    assert ("documentos", "carpeta", "/volume1/documentos") in nombres
    # Un fichero suelto (no dir, no "@") no es un origen.
    assert all(o.nombre != "suelto.txt" for o in origenes)


def test_sin_carpetas_arroba_no_hay_bundle():
    entradas = [_Entrada("web", True)]
    origenes = _origenes_de_volumen("/volume1", entradas)
    assert all(o.tipo != "config" for o in origenes)


# --- Descubrimiento completo (multi-volumen, se detiene en el hueco) ---------

def test_descubrir_para_en_primer_volumen_inexistente():
    fs = {
        "/volume1": ["@appstore/", "web/", "fotos/"],
        "/volume2": ["documentos/"],
        # No hay /volume3 -> la exploración se detiene ahí.
    }
    vols = SynologyConnector().descubrir(_fake_synology(fs))
    assert [v.nombre for v in vols] == ["volume1", "volume2"]
    v1 = {o.nombre for o in vols[0].origenes}
    assert v1 == {"Configuración", "web", "fotos"}
    v2 = {o.nombre for o in vols[1].origenes}
    assert v2 == {"documentos"}  # sin "@" -> sin bundle


def test_fuente_rsync_config_filtra_arroba():
    ruta, filtros = SynologyConnector().fuente_rsync("config", "/volume1")
    assert ruta == "/volume1"
    assert "--include=@*" in filtros and "--exclude=*" in filtros
    # Carpeta normal: sin filtros.
    assert SynologyConnector().fuente_rsync("carpeta", "/volume1/web") == ("/volume1/web", [])
