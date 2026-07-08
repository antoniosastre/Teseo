"""ejecuciones.resultado: nuevo valor "parcial" (rsync código 23)

Revision ID: 0003_resultado_parcial
Revises: 0002_cancel_requested
Create Date: 2026-07-09

rsync sale con 23 cuando la transferencia se completó pero algunos ficheros o
atributos no se pudieron aplicar (p. ej. owner/group hacia un Mac sin root).
Antes se registraba como "fallo"; ahora tiene su propio resultado "parcial".
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_resultado_parcial"
down_revision = "0002_cancel_requested"
branch_labels = None
depends_on = None

_ENUM_VIEJO = sa.Enum("ok", "fallo", "cancelada", name="resultados_ejec")
_ENUM_NUEVO = sa.Enum("ok", "fallo", "cancelada", "parcial", name="resultados_ejec")


def upgrade() -> None:
    op.alter_column(
        "ejecuciones", "resultado",
        existing_type=_ENUM_VIEJO, type_=_ENUM_NUEVO, existing_nullable=True,
    )


def downgrade() -> None:
    # Reclasificar los "parcial" antes de estrechar el enum.
    op.execute("UPDATE ejecuciones SET resultado = 'fallo' WHERE resultado = 'parcial'")
    op.alter_column(
        "ejecuciones", "resultado",
        existing_type=_ENUM_NUEVO, type_=_ENUM_VIEJO, existing_nullable=True,
    )
