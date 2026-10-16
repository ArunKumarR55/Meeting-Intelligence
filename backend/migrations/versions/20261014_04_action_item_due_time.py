"""Persist explicit local clock times for action item deadlines.

Revision ID: 20261014_04
Revises: 20261014_03
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261014_04"
down_revision = "20261014_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable due_time column without changing existing task records."""
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("action_items")}
    if "due_time" not in columns:
        op.add_column("action_items", sa.Column("due_time", sa.Time(), nullable=True))


def downgrade() -> None:
    """Remove the optional clock-time column."""
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("action_items")}
    if "due_time" in columns:
        op.drop_column("action_items", "due_time")
