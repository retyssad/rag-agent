"""Account tables and session ownership.

已存在的库：创建 users/auth_sessions 并给 sessions 加 user_id。
新库（0001 的 create_all 已含新模型）：所有检查均为已存在，本迁移为 no-op。
"""
from alembic import op
import sqlalchemy as sa
from app.db import Base

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 新表（老库缺失时创建；新库已由 0001 create_all 建好）。
    missing_tables = [t for name, t in (('users', Base.metadata.tables['users']),
                                        ('auth_sessions', Base.metadata.tables['auth_sessions']))
                      if name not in tables]
    if missing_tables:
        Base.metadata.create_all(bind, tables=missing_tables)

    # sessions.user_id 列与索引。SQLite 不支持 ALTER 时添加外键约束，
    # 因此列不带 FK（归属校验在应用层统一执行）；新库由 0001 create_all 生成带 FK 的列。
    columns = {c['name'] for c in inspector.get_columns('sessions')}
    if 'user_id' not in columns:
        op.add_column('sessions', sa.Column('user_id', sa.String(64), nullable=True))
    indexes = {i['name'] for i in inspector.get_indexes('sessions')}
    if 'ix_sessions_user_id' not in indexes:
        op.create_index('ix_sessions_user_id', 'sessions', ['user_id'])

    # 登录功能之前的匿名会话没有归属，任何账号都无法访问，直接清理：
    # 按外键依赖顺序删 events → clarifications → requirement_versions → runs → sessions。
    legacy_runs = 'SELECT id FROM runs WHERE session_id IN (SELECT id FROM sessions WHERE user_id IS NULL)'
    for statement in (
        f'DELETE FROM events WHERE run_id IN ({legacy_runs})',
        f'DELETE FROM clarifications WHERE run_id IN ({legacy_runs})',
        f'DELETE FROM requirement_versions WHERE run_id IN ({legacy_runs})',
        'DELETE FROM runs WHERE session_id IN (SELECT id FROM sessions WHERE user_id IS NULL)',
        'DELETE FROM sessions WHERE user_id IS NULL',
    ):
        op.execute(sa.text(statement))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i['name'] for i in inspector.get_indexes('sessions')}
    if 'ix_sessions_user_id' in indexes:
        op.drop_index('ix_sessions_user_id', table_name='sessions')
    columns = {c['name'] for c in inspector.get_columns('sessions')}
    if 'user_id' in columns:
        op.drop_column('sessions', 'user_id')
    tables = set(inspector.get_table_names())
    if 'auth_sessions' in tables:
        op.drop_table('auth_sessions')
    if 'users' in tables:
        op.drop_table('users')
