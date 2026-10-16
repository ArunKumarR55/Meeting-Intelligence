"""Persist inferred and user-confirmed speaker names.

Revision ID: 20261014_01
Revises: 20261013_01
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261014_01"
down_revision = "20261013_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the speaker identity map while preserving existing meeting rows."""
    bind = op.get_bind()
    if "meetings" not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns("meetings")}
    if "speaker_identities" not in columns:
        op.add_column(
            "meetings",
            sa.Column(
                "speaker_identities",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
        with op.batch_alter_table("meetings") as batch_op:
            batch_op.alter_column("speaker_identities", server_default=None)


def downgrade() -> None:
    """Remove speaker identity mappings without changing transcript segments."""
    bind = op.get_bind()
    if "meetings" not in inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in inspect(bind).get_columns("meetings")}
    if "speaker_identities" in columns:
        with op.batch_alter_table("meetings") as batch_op:
            batch_op.drop_column("speaker_identities")
