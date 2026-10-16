"""Persist meeting minutes recipients and delivery state.

Revision ID: 20261014_02
Revises: 20261014_01
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20261014_02"
down_revision = "20261014_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add delivery tracking columns and the saved-recipient directory."""
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    # A previous interrupted SQLite batch migration can leave its empty scratch
    # table behind. Never remove one that contains rows; it may hold recoverable data.
    if "_alembic_tmp_meetings" in tables:
        scratch_rows = bind.execute(
            sa.text('SELECT COUNT(*) FROM "_alembic_tmp_meetings"')
        ).scalar_one()
        if scratch_rows:
            raise RuntimeError(
                "An Alembic meetings scratch table contains data; preserve it and inspect it before retrying."
            )
        op.drop_table("_alembic_tmp_meetings")

    if "meetings" in tables:
        columns = {column["name"] for column in inspector.get_columns("meetings")}
        if "mom_recipients" not in columns:
            op.add_column(
                "meetings",
                sa.Column(
                    "mom_recipients",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                )
            )
        if "mom_delivery_report" not in columns:
            op.add_column(
                "meetings",
                sa.Column(
                    "mom_delivery_report",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'{}'"),
                )
            )
        if "mom_sent_at" not in columns:
            op.add_column(
                "meetings",
                sa.Column("mom_sent_at", sa.DateTime(timezone=True), nullable=True),
            )

    if "mom_email_recipients" not in tables:
        op.create_table(
            "mom_email_recipients",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("email_normalized", sa.String(length=320), nullable=False),
            sa.Column("display_name", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "length(trim(email)) > 2",
                name="ck_mom_email_recipients_email_nonempty",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "email_normalized",
                name="uq_mom_email_recipients_normalized",
            ),
        )
        op.create_index(
            "ix_mom_email_recipients_email_normalized",
            "mom_email_recipients",
            ["email_normalized"],
            unique=False,
        )


def downgrade() -> None:
    """Remove recipient management and delivery metadata."""
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "mom_email_recipients" in tables:
        op.drop_index("ix_mom_email_recipients_email_normalized", table_name="mom_email_recipients")
        op.drop_table("mom_email_recipients")
    if "meetings" in tables:
        columns = {column["name"] for column in inspect(bind).get_columns("meetings")}
        with op.batch_alter_table("meetings") as batch_op:
            for column_name in ("mom_sent_at", "mom_delivery_report", "mom_recipients"):
                if column_name in columns:
                    batch_op.drop_column(column_name)
