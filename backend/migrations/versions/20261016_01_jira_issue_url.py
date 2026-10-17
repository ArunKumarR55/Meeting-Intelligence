"""Store Jira issue links separately from GitHub issue links.

Revision ID: 20261016_01
Revises: 20261014_04
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261016_01"
down_revision = "20261014_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a nullable Jira URL column without modifying GitHub issue data."""
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("action_items")}
    if "jira_issue_url" not in columns:
        op.add_column("action_items", sa.Column("jira_issue_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    """Drop only the Jira-specific URL field."""
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("action_items")}
    if "jira_issue_url" in columns:
        op.drop_column("action_items", "jira_issue_url")
