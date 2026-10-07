"""nilam_guardrails_results.guardrail: one row per guardrail (blank, blur, identity) each time a document is judged

Revision ID: 0011_guardrails_per_check
Revises: 0010_nilam_naming
Create Date: 2026-10-07

Slip gaji has three guardrail services, run by extraction after OCR, and each one's answer is kept on its own row
(`ocr_common.pipeline.tables.guardrails_results_table`), so a rejection can be counted per guardrail with plain SQL.
The column is nullable: a row written before this revision is one merged verdict and has no guardrail. Adding a
nullable column changes the catalog only; the table is not rewritten.

The schema and table names are written out rather than taken from `tables.py`, so this revision keeps acting on
the names it was written for.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_guardrails_per_check"
down_revision: str | None = "0010_nilam_naming"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "nilam_ocr_slipgaji"
TABLES = ("nilam_guardrails_results", "nilam_testing_guardrails_results")


def upgrade() -> None:
    for table in TABLES:
        op.execute(sa.text(f'ALTER TABLE "{SCHEMA}"."{table}" ADD COLUMN IF NOT EXISTS guardrail TEXT'))


def downgrade() -> None:
    for table in TABLES:
        op.execute(sa.text(f'ALTER TABLE "{SCHEMA}"."{table}" DROP COLUMN IF EXISTS guardrail'))
