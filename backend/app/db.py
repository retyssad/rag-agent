from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
from sqlalchemy import (create_engine, String, Text, Float, Integer, JSON, Boolean, DateTime,
                        ForeignKey, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Coach(Base):
    __tablename__ = 'coaches'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[float] = mapped_column(Float)
    districts: Mapped[list] = mapped_column(JSON)
    delivery_modes: Mapped[list] = mapped_column(JSON)
    specialties: Mapped[list] = mapped_column(JSON)
    qualifications: Mapped[list] = mapped_column(JSON)
    courses: Mapped[list] = mapped_column(JSON)
    availability: Mapped[list] = mapped_column(JSON)
    bio: Mapped[str] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Favorite(Base):
    """账号收藏的教练；同一账号对同一教练只能有一条。"""
    __tablename__ = 'favorites'
    __table_args__ = (UniqueConstraint('user_id', 'coach_id', name='uq_favorites_user_coach'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    coach_id: Mapped[str] = mapped_column(ForeignKey('coaches.id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Preregistration(Base):
    """用户账号的预登记（时间安排）记录；状态默认等待教练确认。

    slots 保存具体日期与整点窗口（香港时区），落库前按教练真实档期校验，
    因此这里的记录是"已按教练可约范围登记"，不是已达成的预约。
    """
    __tablename__ = 'preregistrations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    coach_id: Mapped[str] = mapped_column(ForeignKey('coaches.id'), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    slots: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(32), default='pending_coach')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserProfile(Base):
    """跨会话画像缓存：按账号聚合历史需求、收藏与预登记，可随时重算。

    reset_at 非空表示用户主动清空过画像：早于该时间的会话不再计入累积，
    聊天记录本身保留。
    """
    __tablename__ = 'user_profiles'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), unique=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Conversation(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    active_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Run(Base):
    __tablename__ = 'runs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default='created')
    message: Mapped[str] = mapped_column(Text)
    requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 每次 run 一条执行轨迹：阶段耗时、工具调用、供应商调用与 token。单独读取，不随会话列表返回。
    trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RequirementVersion(Base):
    __tablename__ = 'requirement_versions'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Clarification(Base):
    __tablename__ = 'clarifications'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default='pending')
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    coach_id: Mapped[str | None] = mapped_column(ForeignKey('coaches.id'), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(500))
    document_type: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ParentChunk(Base):
    __tablename__ = 'parent_chunks'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    children: Mapped[list] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeState(Base):
    __tablename__ = 'knowledge_state'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    index_revision: Mapped[int] = mapped_column(Integer, default=0)
    index_fingerprint: Mapped[str] = mapped_column(String(255), default='')
    importing: Mapped[bool] = mapped_column(Boolean, default=False)


class UserDocument(Base):
    """用户专属知识库的文档。owner_id 决定归属：检索时只在该账号自己的文档内进行。"""
    __tablename__ = 'user_documents'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(64), default='1')
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserParentChunk(Base):
    """用户文档的父块；子块存 Milvus（集合 user_kb_v1），正文回填到这里。"""
    __tablename__ = 'user_parent_chunks'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('user_documents.id'), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    children: Mapped[list] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserKnowledgeState(Base):
    """每个账号独立的知识索引状态（revision / 是否正在重建）。"""
    __tablename__ = 'user_knowledge_state'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    index_revision: Mapped[int] = mapped_column(Integer, default=0)
    index_fingerprint: Mapped[str] = mapped_column(String(255), default='')
    importing: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Database:
    def __init__(self, url):
        if url.startswith('sqlite:///'):
            Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True,
                                    connect_args={'check_same_thread': False, 'timeout': 30} if url.startswith('sqlite') else {})
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.lock = RLock()

    @contextmanager
    def transaction(self):
        # SQLite 单进程演示序列化写入；MySQL 另用行锁保障多进程提交。
        with self.lock, self.factory.begin() as session:
            yield session
