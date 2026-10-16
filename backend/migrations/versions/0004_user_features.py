"""账号收藏、预登记与跨会话画像表。

新库：0001 的 create_all 已按 app.db 的模型建出 favorites / preregistrations /
user_profiles，本迁移为 no-op。
已有库：仅补齐缺失的新表（照 0002 的 inspector 幂等写法），不改动既有列。
"""
from alembic import op
import sqlalchemy as sa
from app.db import Base

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

NEW_TABLES = ('favorites', 'preregistrations', 'user_profiles')


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    missing = [Base.metadata.tables[name] for name in NEW_TABLES if name not in tables]
    if missing:
        Base.metadata.create_all(bind, tables=missing)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for name in reversed(NEW_TABLES):
        if name in tables:
            op.drop_table(name)
