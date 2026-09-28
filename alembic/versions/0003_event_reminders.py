"""Per-event reminder lead time; null preserves existing category policies."""

import sqlalchemy as sa

from alembic import op

revision = "0003_event_reminders"
down_revision = "0002_security"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("reminder_minutes", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("events") as batch:
        batch.drop_column("reminder_minutes")
