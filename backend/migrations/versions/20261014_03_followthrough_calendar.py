"""Add cross-meeting follow-through, reminder, and calendar storage.

Revision ID: 20261014_03
Revises: 20261014_02
Create Date: 2026-10-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261014_03"
down_revision = "20261014_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create only missing tables so startup create_all remains compatible."""
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())

    if "action_item_drift" not in tables:
        op.create_table(
            "action_item_drift",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("prior_action_item_id", sa.Integer(), nullable=False),
            sa.Column("observed_action_item_id", sa.Integer(), nullable=True),
            sa.Column("observed_in_meeting_id", sa.Integer(), nullable=False),
            sa.Column("drift_type", sa.String(length=32), nullable=False),
            sa.Column("match_score", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("evidence", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["prior_action_item_id"], ["action_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["observed_action_item_id"], ["action_items.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["observed_in_meeting_id"], ["meetings.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "prior_action_item_id", "observed_in_meeting_id",
                name="uq_action_item_drift_prior_meeting",
            ),
        )
        op.create_index(
            "ix_action_item_drift_observed_meeting",
            "action_item_drift", ["observed_in_meeting_id"],
        )

    if "reminder_deliveries" not in tables:
        op.create_table(
            "reminder_deliveries",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("reminder_type", sa.String(length=32), nullable=False),
            sa.Column("reference_key", sa.String(length=128), nullable=False),
            sa.Column("reminder_date", sa.Date(), nullable=False),
            sa.Column("recipient", sa.String(length=320), nullable=False),
            sa.Column("recipient_normalized", sa.String(length=320), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "reminder_type", "reference_key", "reminder_date", "recipient_normalized",
                name="uq_reminder_delivery_daily_recipient",
            ),
        )
        op.create_index(
            "ix_reminder_deliveries_reminder_date", "reminder_deliveries", ["reminder_date"]
        )

    if "calendar_oauth_states" not in tables:
        op.create_table(
            "calendar_oauth_states",
            sa.Column("state", sa.String(length=128), primary_key=True),
            sa.Column("provider", sa.String(length=16), nullable=False),
            sa.Column("pkce_verifier", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "calendar_connections" not in tables:
        op.create_table(
            "calendar_connections",
            sa.Column("provider", sa.String(length=16), primary_key=True),
            sa.Column("account_email", sa.String(length=320), nullable=True),
            sa.Column("access_token_encrypted", sa.Text(), nullable=False),
            sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
            sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "calendar_task_syncs" not in tables:
        op.create_table(
            "calendar_task_syncs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(length=16), nullable=False),
            sa.Column("action_item_id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.String(length=512), nullable=True),
            sa.Column("event_url", sa.String(length=2048), nullable=True),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["action_item_id"], ["action_items.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("provider", "action_item_id", name="uq_calendar_task_sync_provider_item"),
        )

    if "meeting_risk_statuses" not in tables:
        op.create_table(
            "meeting_risk_statuses",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("meeting_id", sa.Integer(), nullable=False),
            sa.Column("risk_index", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("meeting_id", "risk_index", name="uq_meeting_risk_status_index"),
        )
        op.create_index(
            "ix_meeting_risk_status_state", "meeting_risk_statuses", ["status"]
        )


def downgrade() -> None:
    """Remove the newly introduced integration tables."""
    tables = set(inspect(op.get_bind()).get_table_names())
    if "calendar_connections" in tables:
        op.drop_table("calendar_connections")
    if "calendar_task_syncs" in tables:
        op.drop_table("calendar_task_syncs")
    if "calendar_oauth_states" in tables:
        op.drop_table("calendar_oauth_states")
    if "reminder_deliveries" in tables:
        op.drop_index("ix_reminder_deliveries_reminder_date", table_name="reminder_deliveries")
        op.drop_table("reminder_deliveries")
    if "action_item_drift" in tables:
        op.drop_index("ix_action_item_drift_observed_meeting", table_name="action_item_drift")
        op.drop_table("action_item_drift")
    if "meeting_risk_statuses" in tables:
        op.drop_index("ix_meeting_risk_status_state", table_name="meeting_risk_statuses")
        op.drop_table("meeting_risk_statuses")
