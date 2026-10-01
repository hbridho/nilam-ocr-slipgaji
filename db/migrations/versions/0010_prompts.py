"""prompts: the LLM prompts of structuring, readable with PROMPT_SOURCE=db

Revision ID: 0010_prompts
Revises: 0009_guardrails_results_sequence
Create Date: 2026-10-01

The prompt is kept out of code and settings: by default structuring reads `prompts/slip_gaji.v1.md` from its
image (or a mounted ConfigMap); with `PROMPT_SOURCE=db` it reads a row of this table instead, at start-up.
One row per (name, version), never edited in place. No row is seeded here: migrations must not depend on a
service's files. Load the first one with:

    INSERT INTO prompts (name, version, body) VALUES ('slip_gaji', 1, '<text of prompts/slip_gaji.v1.md>');
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_prompts"
down_revision: str | None = "0009_guardrails_results_sequence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_prompts_name_version", "prompts", ["name", "version"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_prompts_name_version", table_name="prompts")
    op.drop_table("prompts")
