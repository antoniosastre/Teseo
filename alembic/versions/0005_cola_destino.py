"""destinos.max_copias y destinos.rsync_remoto

Revision ID: 0005_cola_destino
Revises: 0004_velocidad
Create Date: 2026-11-07

max_copias: copias que pueden escribir a la vez en el destino (cola por destino;
por defecto 1, porque en discos mecánicos varias escrituras simultáneas rinden
mucho menos que encoladas).
rsync_remoto: ruta del rsync que se ejecuta en el destino (--rsync-path), p. ej.
el de Homebrew en un Mac, cuyo rsync de sistema es openrsync.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_cola_destino"
down_revision = "0004_velocidad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "destinos",
        sa.Column("max_copias", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("destinos", sa.Column("rsync_remoto", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("destinos", "rsync_remoto")
    op.drop_column("destinos", "max_copias")
