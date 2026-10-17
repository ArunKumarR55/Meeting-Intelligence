"""Support drift review state and user-managed action-item deadlines.

Revision ID: 20261017_01
Revises: 20261016_01
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261017_01"
down_revision = "20261016_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add review state with existing observations left open for review."""
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("action_item_drift")}
    if "review_status" not in columns:
        op.add_column(
            "action_item_drift",
            sa.Column("review_status", sa.String(length=16), nullable=False, server_default="open"),
        )


def downgrade() -> None:
    """Remove only the drift review state column."""
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("action_item_drift")}
    if "review_status" in columns:
        op.drop_column("action_item_drift", "review_status")
