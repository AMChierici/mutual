"""setup: pool.policy_text + opening_balance ledger kind

Revision ID: 0fe9cc2b3a02
Revises: a5e25b459500
Create Date: 2026-05-09 07:34:10.933924+00:00

Schema delta is just one new column. The new ``opening_balance`` value of
the ``LedgerKind`` enum is enforced at the application layer via
``Enum(..., validate_strings=True)``; no DB CHECK is emitted because
``Enum(native_enum=False)`` defaults to ``create_constraint=False`` in
SQLAlchemy 2.0.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0fe9cc2b3a02'
down_revision: Union[str, None] = 'a5e25b459500'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('pools', schema=None) as batch_op:
        batch_op.add_column(sa.Column('policy_text', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('pools', schema=None) as batch_op:
        batch_op.drop_column('policy_text')
