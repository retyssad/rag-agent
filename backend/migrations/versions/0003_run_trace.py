"""Run execution trace column.

新库（0001 的 create_all 已含 trace 列）：本迁移为 no-op。
已有库：给 runs 加 trace JSON 列，用于每次 run 一条执行轨迹
（阶段耗时、工具调用、供应商调用与 token）。
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('runs')}
    if 'trace' not in columns:
        op.add_column('runs', sa.Column('trace', sa.JSON(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('runs')}
    if 'trace' in columns:
        op.drop_column('runs', 'trace')
