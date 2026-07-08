"""tareas.cancel_requested: bandera para cancelar una copia en curso

Revision ID: 0002_cancel_requested
Revises: 0001_baseline
Create Date: 2026-07-08

La web marca la bandera; el daemon, al sondear la copia, mata el proceso
descolgado en el origen y registra la ejecución como 'cancelada'.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_cancel_requested"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tareas",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("tareas", "cancel_requested")
