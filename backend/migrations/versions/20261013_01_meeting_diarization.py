"""Add persisted transcript segments and diarization status.

Revision ID: 20261013_01
Revises:
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261013_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add fields to legacy installations while supporting fresh databases."""
    bind = op.get_bind()
    tables = inspect(bind).get_table_names()
    if "meetings" not in tables:
        return
    columns = {column["name"] for column in inspect(bind).get_columns("meetings")}
    if "transcript_segments" not in columns:
        op.add_column(
            "meetings",
            sa.Column(
                "transcript_segments",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )
    if "diarization_status" not in columns:
        op.add_column(
            "meetings",
            sa.Column(
                "diarization_status",
                sa.String(length=32),
                nullable=False,
                server_default="not_configured",
            ),
        )
    with op.batch_alter_table("meetings") as batch_op:
        batch_op.alter_column("transcript_segments", server_default=None)
        batch_op.alter_column("diarization_status", server_default=None)


def downgrade() -> None:
    """Remove diarization metadata while retaining all meeting records."""
    bind = op.get_bind()
    if "meetings" not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns("meetings")}
    with op.batch_alter_table("meetings") as batch_op:
        if "diarization_status" in columns:
            batch_op.drop_column("diarization_status")
        if "transcript_segments" in columns:
            batch_op.drop_column("transcript_segments")
