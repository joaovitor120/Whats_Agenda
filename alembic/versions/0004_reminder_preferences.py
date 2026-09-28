"""Multiple event-specific offsets and an exact, exclusive reminder schedule."""

import sqlalchemy as sa

from alembic import op

revision = "0004_reminder_preferences"
down_revision = "0003_event_reminders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("reminder_offsets_minutes", sa.JSON(), nullable=True))
    op.add_column(
        "events",
        sa.Column("reminders_exclusive", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    with op.batch_alter_table("events") as batch:
        batch.drop_column("reminders_exclusive")
        batch.drop_column("reminder_offsets_minutes")
