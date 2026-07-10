"""Auto-provisión de la confianza SSH origen → destino.

El controlador genera un par de claves dedicado a la tarea, instala la **clave
privada en el host origen** y la **pública en authorized_keys del destino**, de
forma que el rsync que se ejecuta en el origen puede empujar al destino sin
contraseñas ni intervención manual.
"""
from __future__ import annotations

import shlex
import threading

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.crypto import SecretBox
from app.db import session_scope
from app.models import Destino, Origen, SshKeypair, Tarea
from app.remote import SshError, SshTarget, connect, run
from app.services import ssh_target_for_destino, ssh_target_for_host

# Serializa la provisión de confianza: dos tareas lanzadas a la vez contra el
# mismo destino tocan el mismo authorized_keys (y validan desde el mismo origen
# escribiendo known_hosts). La provisión es una vez por tarea y dura segundos;
# las copias en sí siguen siendo paralelas.
_PROVISION_LOCK = threading.Lock()


def generate_keypair() -> tuple[str, str]:
    """Genera un par ed25519. Devuelve (privada OpenSSH PEM, pública OpenSSH)."""
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_ssh = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return private_pem, public_ssh


def origin_key_path(client, tarea_id: int) -> str:
    """Ruta absoluta de la clave privada en el host origen."""
    rc, out, _ = run(client, "echo $HOME")
    home = out.strip() or "."
    return f"{home}/.ssh/teseo_task_{tarea_id}"


def _install_private_key_on_origin(origin: SshTarget, tarea_id: int, private_pem: str) -> str:
    with connect(origin) as client:
        key_path = origin_key_path(client, tarea_id)
        run(client, "mkdir -p ~/.ssh && chmod 700 ~/.ssh")
        # Escribe la clave de forma atómica con permisos restrictivos.
        cmd = (
            f"umask 077 && cat > {shlex.quote(key_path)} <<'TESEO_EOF'\n"
            f"{private_pem}\nTESEO_EOF\nchmod 600 {shlex.quote(key_path)}"
        )
        rc, _, err = run(client, cmd)
        if rc != 0:
            raise SshError(f"No se pudo instalar la clave privada en el origen: {err}")
    return key_path


def _install_public_key_on_destino(destino: SshTarget, public_ssh: str) -> None:
    marker = public_ssh.strip()
    with connect(destino) as client:
        run(client, "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys")
        # Evita duplicar la clave si ya está autorizada.
        check = f"grep -qF {shlex.quote(marker)} ~/.ssh/authorized_keys"
        rc, _, _ = run(client, check)
        if rc != 0:
            add = f"printf '%s\\n' {shlex.quote(marker)} >> ~/.ssh/authorized_keys"
            rc2, _, err = run(client, add)
            if rc2 != 0:
                raise SshError(f"No se pudo autorizar la clave en el destino: {err}")


def _validate_cmd(destino: SshTarget, key_path: str) -> str:
    """Comando de validación (extraído para poder testearlo).

    IdentitiesOnly=yes: sin él, el ssh del origen ofrece sus claves por defecto
    ANTES que la de la tarea y puede agotar MaxAuthTries del destino.
    """
    return (
        f"ssh -i {shlex.quote(key_path)} -p {destino.port} "
        f"-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o IdentitiesOnly=yes "
        f"{shlex.quote(destino.usuario + '@' + destino.host)} echo teseo-trust-ok"
    )


def _validate_trust(origin: SshTarget, destino: SshTarget, key_path: str) -> None:
    """Comprueba desde el origen que el SSH al destino funciona con la clave."""
    with connect(origin) as client:
        rc, out, err = run(client, _validate_cmd(destino, key_path), timeout=30)
    if rc != 0 or "teseo-trust-ok" not in out:
        raise SshError(f"La confianza origen→destino no funciona: {err or out}")


def ensure_trust(tarea_id: int, box: SecretBox) -> str:
    """Garantiza que la tarea tiene confianza SSH provisionada.

    Devuelve la ruta de la clave privada en el host origen (para rsync -i).
    Idempotente: si ya está provisionada, solo recalcula la ruta.
    """
    with session_scope() as session:
        tarea = session.get(Tarea, tarea_id)
        if tarea is None:
            raise SshError("Tarea inexistente")
        host = session.get(Origen, tarea.origen_id).volumen.host_origen
        destino = session.get(Destino, tarea.destino_id)
        origin_target = ssh_target_for_host(host, box)
        destino_target = ssh_target_for_destino(destino, box)

        keypair = session.get(SshKeypair, tarea.ssh_keypair_id) if tarea.ssh_keypair_id else None
        if keypair is None:
            private_pem, public_ssh = generate_keypair()
            keypair = SshKeypair(
                private_key_cifrada=box.encrypt(private_pem),
                public_key=public_ssh,
                estado="pendiente",
            )
            session.add(keypair)
            session.flush()
            tarea.ssh_keypair_id = keypair.id
        private_pem = box.decrypt(keypair.private_key_cifrada)
        public_ssh = keypair.public_key
        already = keypair.estado == "provisionada"
        kp_id = keypair.id

    # Operaciones de red fuera de la transacción, SERIALIZADAS entre workers:
    # dos provisiones simultáneas al mismo destino se pisan (authorized_keys,
    # known_hosts del origen) y la segunda falla con "Permission denied".
    with _PROVISION_LOCK:
        key_path = _install_private_key_on_origin(origin_target, tarea_id, private_pem)
        if not already:
            _install_public_key_on_destino(destino_target, public_ssh)
            _validate_trust(origin_target, destino_target, key_path)
            with session_scope() as session:
                kp = session.get(SshKeypair, kp_id)
                if kp:
                    kp.estado = "provisionada"
    return key_path
