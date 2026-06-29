"""Fixtures de test: configuración efímera + BD SQLite en memoria + TestClient."""
from __future__ import annotations

import configparser

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 1) config.ini efímero para que la app se considere instalada.
    cfg = tmp_path / "config.ini"
    p = configparser.ConfigParser()
    p["database"] = {"host": "localhost", "port": "3306", "user": "x", "password": "x", "name": "teseo"}
    p["security"] = {
        "secret_key": Fernet.generate_key().decode(),
        "encryption_key": Fernet.generate_key().decode(),
    }
    with open(cfg, "w") as f:
        p.write(f)
    monkeypatch.setenv("TESEO_CONFIG", str(cfg))

    # Reset de la caché de config (CONFIG_PATH se lee al importar).
    import app.config as config
    config.CONFIG_PATH = cfg

    # 2) Engine SQLite en memoria compartido entre hilos.
    import app.db as db
    from app.auth import hash_password
    from app.models import Admin, Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db._engine = engine
    db._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with db.session_scope() as s:
        s.add(Admin(username="admin", password_hash=hash_password("contrasena1"), email="a@b.com"))

    # 3) TestClient (create_app puede tocar el engine; lo reasignamos después).
    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app()
    db._engine = engine
    db._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return TestClient(app)


@pytest.fixture()
def auth_client(client):
    r = client.post(
        "/login",
        data={"username": "admin", "password": "contrasena1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return client
