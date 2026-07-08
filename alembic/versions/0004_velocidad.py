"""tareas.velocidad: velocidad de transferencia de la copia en curso

Revision ID: 0004_velocidad
Revises: 0003_resultado_parcial
Create Date: 2026-07-09

El daemon la extrae del log de rsync (--info=progress2, p. ej. "4.72MB/s") en
cada sondeo y la limpia al finalizar; la web la muestra junto al porcentaje.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_velocidad"
down_revision = "0003_resultado_parcial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tareas", sa.Column("velocidad", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("tareas", "velocidad")
