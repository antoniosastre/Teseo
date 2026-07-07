"""Login y logout de administradores."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import authenticate, is_locked, register_failure, reset_failures
from app.templating import templates

router = APIRouter()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "desconocido"


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if request.session.get("admin_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    key = _client_key(request)
    if is_locked(key):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Demasiados intentos fallidos. Espera unos minutos."},
            status_code=429,
        )
    admin = authenticate(username, password)
    if admin is None:
        register_failure(key)
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Usuario o contraseña incorrectos."}
        )
    reset_failures(key)
    request.session["admin_id"] = admin.id
    request.session["admin_user"] = admin.username
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
