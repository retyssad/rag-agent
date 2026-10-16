"""用户画像支持“清空并重置累积”：新增 user_profiles.reset_at。

新库：0001 的 create_all 已按 app.db 的模型建出该列，本迁移为 no-op。
已有库：仅补这一列（照 0003 的 inspector 幂等写法）。
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('user_profiles')}
    if 'reset_at' not in columns:
        op.add_column('user_profiles', sa.Column('reset_at', sa.DateTime(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('user_profiles')}
    if 'reset_at' in columns:
        op.drop_column('user_profiles', 'reset_at')
