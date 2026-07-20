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

def test_origenes_solo_carpetas_compartidas():
    """Las carpetas "@" (sistema) NO generan orígenes: la configuración del DSM se
    respalda con la herramienta nativa de Synology, no con rsync (decisión 070926)."""
    entradas = [
        _Entrada("@appstore", True), _Entrada("@database", True),
        _Entrada("web", True), _Entrada("documentos", True), _Entrada("suelto.txt", False),
    ]
    origenes = _origenes_de_volumen("/volume1", entradas)
    nombres = [(o.nombre, o.tipo, o.ruta) for o in origenes]
    assert nombres == [
        ("web", "carpeta", "/volume1/web"),
        ("documentos", "carpeta", "/volume1/documentos"),
    ]
    # Ni bundle "Configuración", ni ficheros sueltos, ni carpetas "@".
    assert all(o.tipo != "config" for o in origenes)


def test_carpeta_destino_teseo_se_ignora():
    """La compartida "Teseo" es el destino de copias por convención: descubrirla
    como origen crearía copias de copias (decisión 071026)."""
    entradas = [_Entrada("Teseo", True), _Entrada("teseo", True), _Entrada("web", True)]
    origenes = _origenes_de_volumen("/volume1", entradas)
    assert [o.nombre for o in origenes] == ["web"]


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
    assert v1 == {"web", "fotos"}  # @appstore se ignora (sin bundle Configuración)
    v2 = {o.nombre for o in vols[1].origenes}
    assert v2 == {"documentos"}


def test_fuente_rsync_config_filtra_arroba():
    # LEGADO: orígenes tipo "config" anteriores al 070926 que sigan en BD.
    ruta, filtros = SynologyConnector().fuente_rsync("config", "/volume1")
    assert ruta == "/volume1"
    assert "--include=@*" in filtros and "--exclude=*" in filtros
    # Las exclusiones van ANTES de los include para que ganen (primer match).
    assert filtros.index("--exclude=@eaDir") < filtros.index("--include=@*")


def test_fuente_rsync_carpeta_excluye_metadatos_y_transitorios():
    """Papelera, snapshots y metadatos de Finder/Explorador nunca se copian."""
    ruta, filtros = SynologyConnector().fuente_rsync("carpeta", "/volume1/web")
    assert ruta == "/volume1/web"
    for excl in ("@eaDir", "#recycle", "#snapshot", ".DS_Store", "._*",
                 "Thumbs.db", "desktop.ini", "~$*", "*.lock", ".TemporaryItems"):
        assert f"--exclude={excl}" in filtros
    # Solo exclusiones: una carpeta normal no lleva ningún --include.
    assert not any(f.startswith("--include") for f in filtros)
