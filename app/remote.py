"""Operaciones SSH remotas mediante paramiko.

Lo usan tanto la web (probar conexión, listar carpetas, leer espacio) como el
daemon (ejecutar rsync en el origen, provisionar claves, monitorizar).
"""
from __future__ import annotations

import hmac
import io
import shlex
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import paramiko


@dataclass
class SshTarget:
    host: str
    port: int
    usuario: str
    auth_method: str          # "key" | "password"
    secret: str | None        # contraseña o clave privada PEM (ya descifrada)
    host_key: str | None = None  # clave de host confiada ("<tipo> <base64>"), si ya se conoce


class SshError(Exception):
    pass


def _hostkey_repr(key: paramiko.PKey) -> str:
    """Representación estable de una clave de host: '<tipo> <base64>'."""
    return f"{key.get_name()} {key.get_base64()}"


class _PinnedPolicy(paramiko.MissingHostKeyPolicy):
    """Verifica la clave de host contra la esperada (pinning).

    Como el cliente arranca sin claves conocidas, paramiko invoca este método en
    CADA conexión, lo que nos permite decidir:
      - Si hay clave esperada: comparar en tiempo constante; si difiere, abortar
        (posible MITM).
      - Si no la hay: confiar en el primer uso (TOFU) y recordar la clave para
        que el llamante pueda persistirla.
    """

    def __init__(self, expected: str | None):
        self.expected = expected.strip() if expected else None
        self.learned: str | None = None

    def missing_host_key(self, client, hostname, key):  # noqa: D102
        presented = _hostkey_repr(key)
        if self.expected:
            if not hmac.compare_digest(presented, self.expected):
                raise SshError(
                    f"La clave del host {hostname} no coincide con la almacenada "
                    "(posible ataque de intermediario). Conexión abortada."
                )
        else:
            self.learned = presented


def _load_pkey(pem: str) -> paramiko.PKey:
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(pem))
        except paramiko.SSHException:
            continue
    raise SshError("No se pudo cargar la clave privada (formato no soportado).")


@contextmanager
def connect(target: SshTarget, timeout: float = 15.0) -> Iterator[paramiko.SSHClient]:
    client = paramiko.SSHClient()
    policy = _PinnedPolicy(target.host_key)
    client.set_missing_host_key_policy(policy)
    try:
        kwargs: dict = dict(
            hostname=target.host,
            port=target.port,
            username=target.usuario,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        if target.auth_method == "key" and target.secret:
            kwargs["pkey"] = _load_pkey(target.secret)
        else:
            kwargs["password"] = target.secret or ""
        client.connect(**kwargs)
        # La clave aprendida (TOFU) queda accesible para que el llamante la persista.
        client._teseo_learned_host_key = policy.learned  # type: ignore[attr-defined]
        yield client
    except SshError:
        raise
    except paramiko.AuthenticationException as exc:
        raise SshError(f"Autenticación fallida: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SshError(str(exc)) from exc
    finally:
        client.close()


def run(client: paramiko.SSHClient, command: str, timeout: float = 30.0) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def test_connection(target: SshTarget) -> tuple[bool, str, str | None]:
    """Prueba la conexión SSH.

    Devuelve (ok, mensaje, host_key_aprendida). ``host_key_aprendida`` sólo es no
    nulo cuando la conexión aún no tenía clave fijada (primer uso), para que el
    llamante pueda persistirla y así fijarla (pinning) en adelante.
    """
    try:
        with connect(target) as client:
            learned = getattr(client, "_teseo_learned_host_key", None)
            rc, out, _ = run(client, "echo teseo-ok")
            if rc == 0 and "teseo-ok" in out:
                return True, "Conexión SSH correcta.", learned
            return False, "Conectado, pero el comando de prueba falló.", learned
    except SshError as exc:
        return False, str(exc), None


def list_directories(target: SshTarget, path: str = "/") -> list[str]:
    """Lista subdirectorios de ``path`` (para elegir la carpeta a copiar)."""
    path = path or "/"
    cmd = f"find {shlex.quote(path)} -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort"
    with connect(target) as client:
        rc, out, _ = run(client, cmd)
    if rc != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


# Medir lo que ocupan las copias recorre TODO el árbol del destino (con los
# históricos por enlaces duros, millones de ficheros): en discos mecánicos puede
# tardar horas. Se hace en el analizador (cada X horas), nunca en el monitor.
DU_TIMEOUT_S = 6 * 3600


def parse_df_posix(salida: str) -> tuple[int, int] | None:
    """(total, libre) en bytes a partir de la salida de ``df -Pk``.

    ``-P`` (formato POSIX, una línea por sistema de ficheros) y ``-k`` (bloques de
    1024 bytes) existen tanto en GNU/Linux como en macOS/BSD; ``-B1`` y
    ``--output`` son solo de GNU. Se busca la secuencia
    ``<bloques> <usados> <disponibles> <capacidad%>`` en vez de contar columnas,
    porque el nombre del sistema de ficheros y el punto de montaje pueden llevar
    espacios.
    """
    for linea in reversed(salida.strip().splitlines()):
        tokens = linea.split()
        for i in range(len(tokens) - 3):
            total, usados, libres, capacidad = tokens[i:i + 4]
            if total.isdigit() and usados.isdigit() and libres.isdigit() and capacidad.endswith("%"):
                return int(total) * 1024, int(libres) * 1024
    return None


def disk_space(target: SshTarget, path: str) -> tuple[int, int] | None:
    """Espacio (total, libre) del sistema de ficheros que contiene ``path``.

    Barato (solo ``df``): apto para el monitor de cada pocos minutos. Lanza
    ``SshError`` si no hay conexión; devuelve None si conecta pero no se puede
    interpretar ``df`` (el destino sigue siendo accesible).
    """
    qpath = shlex.quote(path)
    with connect(target) as client:
        run(client, f"mkdir -p {qpath}")
        rc, out, _ = run(client, f"df -Pk {qpath}")
    return parse_df_posix(out) if rc == 0 else None


def backups_size(target: SshTarget, path: str, timeout: float = DU_TIMEOUT_S) -> int | None:
    """Bytes que ocupan las copias en ``path`` (``du -sk``, válido en Linux y macOS).

    Cuenta una sola vez los ficheros con varios enlaces duros (snapshots
    incrementales). Puede tardar mucho: ver ``DU_TIMEOUT_S``.
    """
    qpath = shlex.quote(path)
    with connect(target) as client:
        _, out, _ = run(client, f"du -sk {qpath} 2>/dev/null | cut -f1", timeout=timeout)
    out = out.strip()
    return int(out) * 1024 if out.isdigit() else None
