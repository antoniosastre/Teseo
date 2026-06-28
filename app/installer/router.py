"""Rutas del asistente de instalación."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import DatabaseConfig, SmtpConfig, config_exists
from app.installer.service import run_install, test_connection
from app.templating import templates

router = APIRouter()


@router.get("/install", response_class=HTMLResponse)
async def install_form(request: Request):
    if config_exists():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("install.html", {"request": request, "error": None, "form": {}})


@router.post("/install/test", response_class=HTMLResponse)
async def install_test(
    request: Request,
    db_host: str = Form("localhost"),
    db_port: int = Form(3306),
    db_user: str = Form(...),
    db_password: str = Form(""),
    db_name: str = Form(...),
):
    db = DatabaseConfig(host=db_host, port=db_port, user=db_user, password=db_password, name=db_name)
    ok, message = test_connection(db)
    return templates.TemplateResponse(
        "install.html",
        {
            "request": request,
            "error": None if ok else message,
            "test_ok": ok,
            "test_message": message,
            "form": {
                "db_host": db_host, "db_port": db_port, "db_user": db_user,
                "db_password": db_password, "db_name": db_name,
            },
        },
    )


@router.post("/install", response_class=HTMLResponse)
async def install_submit(
    request: Request,
    db_host: str = Form("localhost"),
    db_port: int = Form(3306),
    db_user: str = Form(...),
    db_password: str = Form(""),
    db_name: str = Form(...),
    admin_user: str = Form(...),
    admin_password: str = Form(...),
    admin_password2: str = Form(...),
    admin_email: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_sender: str = Form(""),
):
    if config_exists():
        return RedirectResponse("/", status_code=303)

    form = {
        "db_host": db_host, "db_port": db_port, "db_user": db_user,
        "db_password": db_password, "db_name": db_name,
        "admin_user": admin_user, "admin_email": admin_email,
        "smtp_host": smtp_host, "smtp_port": smtp_port,
        "smtp_user": smtp_user, "smtp_sender": smtp_sender,
    }

    def fail(msg: str):
        return templates.TemplateResponse(
            "install.html", {"request": request, "error": msg, "form": form}
        )

    if admin_password != admin_password2:
        return fail("Las contraseñas de administrador no coinciden.")
    if len(admin_password) < 8:
        return fail("La contraseña de administrador debe tener al menos 8 caracteres.")

    db = DatabaseConfig(host=db_host, port=db_port, user=db_user, password=db_password, name=db_name)
    ok, message = test_connection(db)
    if not ok:
        return fail(message)

    smtp = SmtpConfig(
        host=smtp_host, port=smtp_port, user=smtp_user,
        password=smtp_password, sender=smtp_sender,
    )

    try:
        run_install(db, admin_user, admin_password, admin_email, smtp if smtp.host else None)
    except Exception as exc:  # noqa: BLE001
        return fail(f"Error durante la instalación: {exc}")

    return RedirectResponse("/login", status_code=303)
