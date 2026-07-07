"""baseline: esquema inicial (creado por create_all en la instalación)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-07

Baseline VACÍO a propósito: el esquema inicial lo crea el asistente de instalación
con ``Base.metadata.create_all``. Ejecutar ``alembic upgrade head`` sobre ese
esquema es un no-op idempotente que sella la BD en esta revisión; las migraciones
futuras (cambios de columnas, tablas nuevas por versión, etc.) cuelgan de aquí.
"""
from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
