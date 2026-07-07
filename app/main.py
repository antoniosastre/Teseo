"""Punto de entrada de la aplicación web FastAPI de Teseo."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import config_exists, load_config
from app.db import init_engine
from app.deps import RedirectException
from app.templating import templates

# Rutas que deben ser accesibles aunque la app no esté instalada / sin sesión.
INSTALL_PATHS = ("/install", "/static")
PUBLIC_PATHS = ("/login", "/logout", "/static", "/install")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Teseo — Panel de copias rsync")

    # Clave de sesión: de la config si existe, si no, una efímera (solo instalador).
    config = load_config()
    secret_key = config.secret_key if config and config.secret_key else "teseo-setup-ephemeral-key"
    # En producción (tras TLS) config.https_only=true marca la cookie como Secure.
    https_only = bool(config and config.https_only)
    app.add_middleware(SessionMiddleware, secret_key=secret_key, https_only=https_only)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Inicializa el engine si ya está configurado (no falla si no lo está).
    if config_exists():
        try:
            init_engine()
        except Exception:  # noqa: BLE001 - se gestionará al primer acceso
            pass

    @app.exception_handler(RedirectException)
    async def _redirect_handler(request: Request, exc: RedirectException):
        return RedirectResponse(exc.location, status_code=303)

    @app.middleware("http")
    async def gate_middleware(request: Request, call_next):
        """Si la app no está instalada, fuerza el paso por el asistente."""
        path = request.url.path
        if not config_exists() and not path.startswith(INSTALL_PATHS):
            return RedirectResponse("/install", status_code=303)
        return await call_next(request)

    # Routers
    from app.installer.router import router as installer_router
    from app.routers.auth import router as auth_router
    from app.routers.dashboard import router as dashboard_router
    from app.routers.destinos import router as destinos_router
    from app.routers.origenes import router as origenes_router
    from app.routers.tareas import router as tareas_router
    from app.routers.estado import router as estado_router
    from app.routers.ubicaciones import router as ubicaciones_router
    from app.routers.ajustes import router as ajustes_router

    app.include_router(installer_router)
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(destinos_router)
    app.include_router(origenes_router)
    app.include_router(tareas_router)
    app.include_router(estado_router)
    app.include_router(ubicaciones_router)
    app.include_router(ajustes_router)

    return app


app = create_app()
