"""Tests de la auto-provisión de confianza SSH (daemon/keyprov.py)."""
from __future__ import annotations

from app.remote import SshTarget
from daemon.keyprov import _PROVISION_LOCK, _validate_cmd, generate_keypair


def test_generate_keypair_formatos():
    private_pem, public_ssh = generate_keypair()
    assert "OPENSSH PRIVATE KEY" in private_pem
    assert public_ssh.startswith("ssh-ed25519 ")


def test_validate_cmd_endurecido():
    destino = SshTarget(host="mac.local", port=2222, usuario="bk",
                        auth_method="password", secret=None)
    cmd = _validate_cmd(destino, "/root/.ssh/teseo_task_7")
    assert cmd.startswith("ssh -i /root/.ssh/teseo_task_7 -p 2222")
    # Endurecimiento: solo la clave de la tarea (no agotar MaxAuthTries del
    # destino ofreciendo claves por defecto) y sin prompts interactivos.
    assert "-o IdentitiesOnly=yes" in cmd
    assert "-o BatchMode=yes" in cmd
    assert "bk@mac.local" in cmd and "echo teseo-trust-ok" in cmd


def test_lock_de_provision_existe():
    # La provisión debe estar serializada entre workers del daemon (dos tareas
    # lanzadas a la vez contra el mismo destino se pisan authorized_keys).
    assert hasattr(_PROVISION_LOCK, "acquire") and hasattr(_PROVISION_LOCK, "release")
