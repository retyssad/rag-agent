"""用户专属知识库表：user_documents / user_parent_chunks / user_knowledge_state。

新库：0001 的 create_all 已按 app.db 的模型建出，本迁移为 no-op。
已有库：仅补齐缺失的新表（照 0002/0004 的 inspector 幂等写法）。
"""
from alembic import op
import sqlalchemy as sa
from app.db import Base

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

NEW_TABLES = ('user_documents', 'user_parent_chunks', 'user_knowledge_state')


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
