"""Operaciones SSH remotas mediante paramiko.

Lo usan tanto la web (probar conexión, listar carpetas, leer espacio) como el
daemon (ejecutar rsync en el origen, provisionar claves, monitorizar).
"""
from __future__ import annotations

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


class SshError(Exception):
    pass


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
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
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
        yield client
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


def test_connection(target: SshTarget) -> tuple[bool, str]:
    try:
        with connect(target) as client:
            rc, out, _ = run(client, "echo teseo-ok")
            if rc == 0 and "teseo-ok" in out:
                return True, "Conexión SSH correcta."
            return False, "Conectado, pero el comando de prueba falló."
    except SshError as exc:
        return False, str(exc)


def list_directories(target: SshTarget, path: str = "/") -> list[str]:
    """Lista subdirectorios de ``path`` (para elegir la carpeta a copiar)."""
    path = path or "/"
    cmd = f"find {shlex.quote(path)} -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort"
    with connect(target) as client:
        rc, out, _ = run(client, cmd)
    if rc != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


@dataclass
class DiskUsage:
    total: int  # bytes del filesystem que contiene la carpeta
    free: int   # bytes libres
    backups: int  # bytes ocupados por la carpeta de backups (du)


def disk_usage(target: SshTarget, path: str) -> DiskUsage:
    """Espacio del filesystem (df) y ocupado por la carpeta de backups (du)."""
    qpath = shlex.quote(path)
    with connect(target) as client:
        run(client, f"mkdir -p {qpath}")
        # df en bloques de 1 byte: total y disponible del filesystem.
        rc, out, _ = run(client, f"df -B1 --output=size,avail {qpath} | tail -1")
        total = free = 0
        if rc == 0:
            nums = out.split()
            if len(nums) >= 2:
                total, free = int(nums[0]), int(nums[1])
        # du de la carpeta de backups (puede ser costoso; el caller lo cachea).
        rc, out, _ = run(client, f"du -sb {qpath} 2>/dev/null | cut -f1")
        backups = int(out.strip()) if rc == 0 and out.strip().isdigit() else 0
    return DiskUsage(total=total, free=free, backups=backups)
