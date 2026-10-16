import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import delete, select, update
from app.db import (Database, Coach, Conversation, Run, Clarification, Event, Document,
                    ParentChunk, KnowledgeState, RequirementVersion, Favorite, Preregistration,
                    UserProfile, User, UserDocument, UserParentChunk, UserKnowledgeState, uid, now)
from app.schemas import Requirements, RequirementPatch

HK = ZoneInfo('Asia/Hong_Kong')
WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
PREREG_STATUS_LABEL = {'pending_coach': '已登记 · 待教练确认'}
SCHEDULE_DAYS = 28
NOTE_MAX = 200
# 备注里出现这些内容说明模型把自己的解释或内部标识写进了用户可见字段，
# 属于实现细节泄漏，服务端直接丢弃整条备注而不是展示给用户。
_NOTE_LEAK = re.compile(
    r'[0-9a-f]{16,}'                      # 用户/session 等 hex 标识
    r'|session|用户\s*[0-9a-f]'           # 内部字段名
    r'|未指定具体日期|未替用户|完整登记|此处按|待教练与用户确认'
)


def sanitize_note(note):
    """只保留用户可读的简短备注；疑似模型解释或内部标识则整条丢弃。"""
    text = (note or '').strip()
    if not text:
        return ''
    if len(text) > NOTE_MAX or _NOTE_LEAK.search(text):
        return ''
    return text


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class ValidationFailed(ValueError):
    """预登记等输入校验失败；作为 ValueError 子类，工具层可让模型修正参数后重试。"""


def empty_profile():
    """画像的空形状：保持与 _compute_profile 相同的键，前端无需区分空/非空。"""
    return {'goals': [], 'districts': [], 'delivery_modes': [], 'preferences': [],
            'self_reported_conditions': [], 'budget': None, 'sessions': 0,
            'requirement_versions': 0, 'favorite_coaches': [], 'preregistered_coaches': []}


def hk_today(today=None):
    return today or datetime.now(HK).date()


def schedule_window(today=None):
    """未来 4 周的可选日期范围：明天起 28 天（含），香港时区。"""
    today = hk_today(today)
    return today + timedelta(days=1), today + timedelta(days=SCHEDULE_DAYS)


def day_view(coach, day):
    """把某一天与教练档期对照，给出可约窗口。"""
    blocks = [w for w in coach['availability'] if day.weekday() in w['days']]
    return {'date': day.isoformat(), 'weekday': day.weekday(),
            'label': WEEKDAY_NAMES[day.weekday()], 'is_available': bool(blocks),
            'windows': [{'start': w['start'], 'end': w['end']} for w in blocks]}


def validate_slots(coach, slots, today=None):
    """按教练真实档期与 4 周范围校验预登记时段；非法直接拒绝，不静默放宽。"""
    start, end = schedule_window(today)
    cleaned = []
    for slot in slots:
        try:
            day = date.fromisoformat(slot['date'])
        except (TypeError, ValueError) as error:
            raise ValidationFailed('预登记日期格式不正确') from error
        if not start <= day <= end:
            raise ValidationFailed(f'{slot["date"]} 不在未来 4 周的可预登记范围内')
        if slot['start'] >= slot['end']:
            raise ValidationFailed('时间段无效，开始必须早于结束')
        blocks = [w for w in coach['availability'] if day.weekday() in w['days']]
        if not any(slot['start'] >= w['start'] and slot['end'] <= w['end'] for w in blocks):
            raise ValidationFailed(f'{slot["date"]} {slot["start"]}:00–{slot["end"]}:00 不在该教练可约时间内')
        cleaned.append({'date': slot['date'], 'start': int(slot['start']), 'end': int(slot['end'])})
    return cleaned


def asdict(row):
    from datetime import datetime
    return {c.name: (value.isoformat() + 'Z' if isinstance(value, datetime) else value)
            for c in row.__table__.columns for value in [getattr(row, c.name)]}


def merge_requirements(current, patch, version):
    values = dict(current)
    values.update(RequirementPatch.model_validate(patch).model_dump(exclude_unset=True))
    unrestricted = set(current.get('unrestricted_fields', []))
    for key, value in patch.items():
        if value is None or value == []:
            unrestricted.add(key)
        else:
            unrestricted.discard(key)
    values['unrestricted_fields'] = sorted(unrestricted)
    for name in ('preferences', 'self_reported_conditions', 'available_windows'):
        if values.get(name) is None:
            values[name] = []
    values['version'] = version
    return Requirements.model_validate(values).model_dump()


def time_matches(slots, windows):
    # 用户给出的可用时段是可选范围：至少一个连续小时的交集才算可安排。
    return not windows or any(set(s['days']) & set(w['days']) and
                              min(s['end'], w['end']) - max(s['start'], w['start']) >= 1
                              for s in slots for w in windows)


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def create_session(self, user_id=None):
        with self.db.transaction() as s:
            row = Conversation(user_id=user_id, requirements=Requirements().model_dump())
            s.add(row)
            s.flush()
            result = asdict(row)
            result.pop('user_id', None)
            # 与 GET /sessions/{id} 保持同一形状，前端可安全读取 runs。
            result['runs'] = []
            return result

    def session(self, session_id, user_id=None):
        with self.db.transaction() as s:
            row = s.get(Conversation, session_id)
            if not row or (user_id is not None and row.user_id != user_id):
                raise NotFound('会话不存在')
            result = asdict(row)
            result.pop('user_id', None)
            result['runs'] = [self._run_view(s, run) for run in s.scalars(
                select(Run).where(Run.session_id == session_id).order_by(Run.created_at))]
            return result

    def list_sessions(self, user_id):
        with self.db.transaction() as s:
            rows = s.scalars(select(Conversation).where(Conversation.user_id == user_id)).all()
            result = []
            for row in rows:
                runs = s.scalars(select(Run).where(Run.session_id == row.id)
                                 .order_by(Run.created_at)).all()
                first = next((r.message for r in runs), '')
                view = asdict(row)
                for key in ('requirements', 'version', 'active_run_id', 'user_id'):
                    view.pop(key, None)
                view['title'] = (first[:20] + '…') if len(first) > 20 else (first or '新对话')
                view['updated_at'] = max((r.updated_at for r in runs), default=row.created_at).isoformat() + 'Z'
                result.append(view)
            result.sort(key=lambda v: v['updated_at'], reverse=True)
            return result

    def delete_session(self, session_id, user_id):
        with self.db.transaction() as s:
            row = s.get(Conversation, session_id)
            if not row or (user_id is not None and row.user_id != user_id):
                raise NotFound('会话不存在')
            run_ids = [r for (r,) in s.execute(select(Run.id).where(Run.session_id == session_id))]
            if run_ids:
                s.execute(delete(Event).where(Event.run_id.in_(run_ids)))
                s.execute(delete(Clarification).where(Clarification.run_id.in_(run_ids)))
                s.execute(delete(RequirementVersion).where(RequirementVersion.run_id.in_(run_ids)))
                s.execute(delete(Run).where(Run.session_id == session_id))
            s.delete(row)
            return {'deleted': session_id}

    def _session_owned(self, s, session_id, user_id):
        row = s.get(Conversation, session_id)
        if not row or (user_id is not None and row.user_id != user_id):
            raise NotFound('会话不存在')
        return row

    def _run_view(self, s, row):
        result = asdict(row)
        # trace 只通过 GET /api/runs/{id}/trace 单独读取；不放进会话/运行视图，
        # 否则 SSE 每 300ms 的轮询会反复拉取全量轨迹。
        result.pop('trace', None)
        pending = s.scalars(select(Clarification).where(
            Clarification.run_id == row.id, Clarification.status == 'pending')).first()
        result['clarification'] = asdict(pending) if pending else None
        return result

    def run(self, run_id, user_id=None):
        with self.db.transaction() as s:
            row = s.get(Run, run_id)
            if not row:
                raise NotFound('运行不存在')
            if user_id is not None:
                self._session_owned(s, row.session_id, user_id)
            return self._run_view(s, row)

    def create_run(self, session_id, message, patch=None, coach_ids=None, execution_scope='api', user_id=None):
        with self.db.transaction() as s:
            conversation = s.scalars(select(Conversation).where(Conversation.id == session_id).with_for_update()).first()
            if not conversation or (user_id is not None and conversation.user_id != user_id):
                raise NotFound('会话不存在')
            if conversation.active_run_id:
                previous = s.get(Run, conversation.active_run_id)
                if previous and previous.status in ('created', 'running', 'waiting_for_user'):
                    previous.status = 'cancelled'
                    s.execute(update(Clarification).where(Clarification.run_id == previous.id,
                        Clarification.status == 'pending').values(status='expired'))
                    s.add(Event(run_id=previous.id, kind='cancelled', data={'message': '新消息已替代此任务'}))
            conversation.version += 1
            req = merge_requirements(conversation.requirements, patch or {}, conversation.version)
            previous_ids = []
            previous_completed = s.scalars(select(Run).where(Run.session_id == session_id,
                Run.status == 'completed').order_by(Run.created_at.desc())).first()
            if previous_completed and previous_completed.result:
                previous_ids = [c['id'] for c in previous_completed.result.get('candidates', [])]
            row = Run(session_id=session_id, version=conversation.version, message=message,
                      requirements=req, context={'explicit_patch': patch or {}, 'coach_ids': coach_ids or [],
                                                 'previous_coach_ids': previous_ids, 'execution_scope': execution_scope})
            s.add(row)
            s.flush()
            conversation.active_run_id = row.id
            conversation.requirements = req
            s.add(RequirementVersion(run_id=row.id, version=row.version, snapshot=req))
            s.add(Event(run_id=row.id, kind='created', data={'message': '已收到需求'}))
            return self._run_view(s, row)

    def _current(self, s, run_id):
        row = s.get(Run, run_id)
        if not row:
            raise NotFound('运行不存在')
        conv = s.scalars(select(Conversation).where(Conversation.id == row.session_id).with_for_update()).one()
        # 取得会话锁后刷新，避免读取取消之前的快照。
        s.refresh(row)
        if conv.active_run_id != row.id or row.status in ('cancelled', 'completed', 'failed'):
            raise Conflict('任务已结束或已被新需求替代')
        return row, conv

    def save_state(self, run_id, *, status=None, requirements=None, context=None, result=None, error=None, trace=None):
        with self.db.transaction() as s:
            row, conv = self._current(s, run_id)
            if requirements is not None:
                row.requirements = requirements
                conv.requirements = requirements
                s.add(RequirementVersion(run_id=row.id, version=row.version, snapshot=requirements))
            if context is not None:
                row.context = context
            if result is not None:
                row.result = result
            if error is not None:
                row.error = error
            if trace is not None:
                row.trace = trace
            if status:
                row.status = status
            row.updated_at = now()

    def run_trace(self, run_id, user_id=None):
        with self.db.transaction() as s:
            row = s.get(Run, run_id)
            if not row:
                raise NotFound('运行不存在')
            if user_id is not None:
                self._session_owned(s, row.session_id, user_id)
            return row.trace or {}

    def save_trace(self, run_id, trace):
        """诊断数据单独写入，不受运行状态锁约束：取消/替代后仍能留下最后轨迹。"""
        with self.db.transaction() as s:
            row = s.get(Run, run_id)
            if row:
                row.trace = trace

    def add_event(self, run_id, kind, data):
        with self.db.transaction() as s:
            row, _ = self._current(s, run_id)
            event = Event(run_id=row.id, kind=kind, data=data)
            s.add(event)
            s.flush()
            return {'id': event.id, 'kind': kind, 'data': data}

    def terminal_event(self, run_id, kind, message):
        with self.db.transaction() as s:
            s.add(Event(run_id=run_id, kind=kind, data={'message': message}))

    def events(self, run_id, after=0):
        with self.db.transaction() as s:
            return [asdict(e) for e in s.scalars(select(Event).where(Event.run_id == run_id,
                    Event.id > after).order_by(Event.id).limit(200))]

    def pause(self, run_id, args):
        with self.db.transaction() as s:
            row, _ = self._current(s, run_id)
            request = Clarification(run_id=run_id, version=row.version, **args)
            s.add(request)
            row.status = 'waiting_for_user'
            s.flush()
            s.add(Event(run_id=run_id, kind='waiting_for_user', data={'message': request.question,
                                                                   'request_id': request.id}))
            return asdict(request)

    def reply(self, request_id, answer, patch, user_id=None):
        with self.db.transaction() as s:
            # 所有运行变更按会话锁→人工请求锁顺序执行，避免 MySQL 死锁。
            preview = s.get(Clarification, request_id)
            if not preview:
                raise NotFound('问题不存在')
            row = s.get(Run, preview.run_id)
            if user_id is not None:
                self._session_owned(s, row.session_id, user_id)
            conv = s.scalars(select(Conversation).where(Conversation.id == row.session_id).with_for_update()).one()
            request = s.scalars(select(Clarification).where(Clarification.id == request_id).with_for_update()).one()
            s.refresh(row)
            if request.status == 'answered':
                if request.answer != answer or (request.patch or {}) != (patch or {}):
                    raise Conflict('此问题已提交，不能更换答案；请发送新消息')
                return self._run_view(s, row), False
            if request.status != 'pending' or row.status != 'waiting_for_user' or conv.active_run_id != row.id:
                raise Conflict('此问题已过期，请在当前会话发送新消息')
            request.status, request.answer, request.patch = 'answered', answer, patch or {}
            row.context = {**row.context, 'resume': {'answer': answer, 'patch': patch or {},
                                                  'reason': request.reason, 'question': request.question}}
            row.status = 'created'
            s.add(Event(run_id=row.id, kind='resumed', data={'message': '已收到补充信息'}))
            return self._run_view(s, row), True

    def cancel(self, run_id, user_id=None):
        with self.db.transaction() as s:
            row = s.get(Run, run_id)
            if not row:
                raise NotFound('运行不存在')
            if user_id is not None:
                self._session_owned(s, row.session_id, user_id)
            conv = s.scalars(select(Conversation).where(Conversation.id == row.session_id).with_for_update()).one()
            s.refresh(row)
            if row.status in ('created', 'running', 'waiting_for_user'):
                row.status = 'cancelled'
                s.execute(update(Clarification).where(Clarification.run_id == run_id,
                    Clarification.status == 'pending').values(status='expired'))
                s.add(Event(run_id=run_id, kind='cancelled', data={'message': '任务已取消'}))
            return self._run_view(s, row)

    def recover_interrupted(self):
        with self.db.transaction() as s:
            for row in s.scalars(select(Run).where(Run.status.in_(['created', 'running']))):
                if row.context.get('execution_scope', 'api') != 'api':
                    continue
                row.status = 'failed'
                row.error = '服务重启中断了执行，请重新发送消息；已等待的人工问题仍可恢复。'
                s.add(Event(run_id=row.id, kind='failed', data={'message': row.error}))

    def search_coaches(self, filters, coach_ids=None):
        with self.db.transaction() as s:
            query = select(Coach).order_by(Coach.price, Coach.id)
            if filters.get('budget_max') is not None:
                query = query.where(Coach.price <= filters['budget_max'])
            if coach_ids:
                query = query.where(Coach.id.in_(coach_ids))
            rows = [asdict(c) for c in s.scalars(query)]
        return [c for c in rows if (not filters.get('district') or filters['district'] in c['districts'])
                and (not filters.get('delivery_mode') or filters['delivery_mode'] in c['delivery_modes'])
                and time_matches(c['availability'], filters.get('available_windows') or [])]

    def coach(self, coach_id):
        with self.db.transaction() as s:
            row = s.get(Coach, coach_id)
            if not row:
                raise NotFound('教练不存在')
            return asdict(row)

    def knowledge_state(self):
        with self.db.transaction() as s:
            row = s.get(KnowledgeState, 1)
            return asdict(row) if row else {'revision': 0, 'index_revision': 0, 'index_fingerprint': '', 'importing': False}

    def active_children(self, coach_ids=None, filters=None):
        with self.db.transaction() as s:
            rows = s.execute(select(ParentChunk, Document).join(Document).where(
                ParentChunk.is_active.is_(True), Document.is_active.is_(True))).all()
            result = []
            for parent, doc in rows:
                if parent.version != doc.version or (coach_ids is not None and (doc.coach_id or '') not in coach_ids):
                    continue
                if filters and any(getattr(doc, k, None) != v for k, v in filters.items()):
                    continue
                for child in parent.children:
                    result.append({**child, 'coach_id': doc.coach_id or '', 'parent_id': parent.id,
                        'document_id': doc.id, 'document_version': doc.version, 'document_type': doc.document_type,
                        'source_title': doc.title, 'source_reference': doc.source,
                        'is_active': True, 'updated_at': doc.updated_at.isoformat()})
            return result

    def parent(self, parent_id, version=None):
        with self.db.transaction() as s:
            row = s.execute(select(ParentChunk, Document).join(Document).where(ParentChunk.id == parent_id,
                            ParentChunk.is_active.is_(True), Document.is_active.is_(True))).first()
            if not row or row[0].version != row[1].version or (version and version != row[1].version):
                raise NotFound('来源已更新或下架，请重新检索')
            parent, doc = row
            return {'id': parent.id, 'document_id': doc.id, 'coach_id': doc.coach_id,
                    'title': parent.title, 'source_title': doc.title, 'source': doc.source,
                    'version': doc.version, 'text': parent.text, 'is_demo': doc.is_demo}

    # ------------------------------------------------------------------ 账号关联
    def session_user_id(self, session_id):
        """Run 自身不带归属，交给 orchestrator 时用于取账号画像。"""
        with self.db.transaction() as s:
            row = s.get(Conversation, session_id)
            return row.user_id if row else None

    # ------------------------------------------------------------------ 用户专属知识库
    def user_knowledge_state(self, user_id):
        with self.db.transaction() as s:
            return self._user_kb_state(s, user_id)

    def _user_kb_state(self, s, user_id):
        row = s.get(UserKnowledgeState, user_id)
        if not row:
            return {'user_id': user_id, 'revision': 0, 'index_revision': 0,
                    'index_fingerprint': '', 'importing': False}
        return asdict(row)

    def _mark_user_kb(self, s, user_id, *, importing=None, index_revision=None, fingerprint=None, bump=False):
        row = s.get(UserKnowledgeState, user_id)
        if not row:
            row = UserKnowledgeState(user_id=user_id, revision=0, index_revision=0,
                                     index_fingerprint='', importing=False)
            s.add(row)
            s.flush()
        if bump:
            row.revision += 1
        if importing is not None:
            row.importing = importing
        if index_revision is not None:
            row.index_revision = index_revision
        if fingerprint is not None:
            row.index_fingerprint = fingerprint
        row.updated_at = now()
        return asdict(row)

    def add_user_document(self, user_id, *, title, filename, file_type, byte_size, content, content_hash, version='1'):
        """新增或按 (owner, 同名校) 覆盖一份用户文档，并重建其父块。"""
        with self.db.transaction() as s:
            existing = s.scalars(select(UserDocument).where(
                UserDocument.owner_id == user_id, UserDocument.filename == filename,
                UserDocument.is_active.is_(True))).first()
            if existing:
                s.execute(update(UserParentChunk).where(UserParentChunk.document_id == existing.id)
                          .values(is_active=False))
                existing.title, existing.file_type, existing.byte_size = title, file_type, byte_size
                existing.content, existing.content_hash = content, content_hash
                existing.version = version
                existing.updated_at = now()
                doc = existing
            else:
                doc = UserDocument(owner_id=user_id, title=title, filename=filename, file_type=file_type,
                                   byte_size=byte_size, content=content, content_hash=content_hash,
                                   version=version, updated_at=now())
                s.add(doc)
            s.flush()
            self._mark_user_kb(s, user_id, bump=True)
            return {'id': doc.id, 'title': doc.title, 'filename': doc.filename, 'file_type': doc.file_type}

    def add_user_parents(self, user_id, document_id, parents):
        with self.db.transaction() as s:
            for parent in parents:
                s.merge(UserParentChunk(owner_id=user_id, document_id=document_id, **parent))

    def list_user_documents(self, user_id):
        with self.db.transaction() as s:
            rows = s.scalars(select(UserDocument).where(
                UserDocument.owner_id == user_id, UserDocument.is_active.is_(True))
                .order_by(UserDocument.created_at.desc())).all()
            chunks = s.execute(select(UserParentChunk.document_id).where(
                UserParentChunk.owner_id == user_id, UserParentChunk.is_active.is_(True))).all()
            counts = Counter(doc_id for (doc_id,) in chunks)
            return [{**asdict(row), 'chunks': counts.get(row.id, 0)} for row in rows]

    def user_document(self, user_id, document_id):
        with self.db.transaction() as s:
            row = s.get(UserDocument, document_id)
            if not row or row.owner_id != user_id or not row.is_active:
                raise NotFound('文档不存在')
            return asdict(row)

    def delete_user_document(self, user_id, document_id):
        with self.db.transaction() as s:
            row = s.get(UserDocument, document_id)
            if not row or row.owner_id != user_id or not row.is_active:
                raise NotFound('文档不存在')
            row.is_active = False
            row.updated_at = now()
            s.execute(update(UserParentChunk).where(UserParentChunk.document_id == document_id)
                      .values(is_active=False))
            self._mark_user_kb(s, user_id, bump=True)
            return {'deleted': document_id}

    def active_user_children(self, user_id, document_ids=None):
        with self.db.transaction() as s:
            query = select(UserParentChunk).where(UserParentChunk.owner_id == user_id,
                                                  UserParentChunk.is_active.is_(True))
            if document_ids is not None:
                query = query.where(UserParentChunk.document_id.in_(document_ids))
            rows = s.scalars(query).all()
            docs = {d.id: d for d in s.scalars(select(UserDocument).where(
                UserDocument.owner_id == user_id, UserDocument.is_active.is_(True)))}
            result = []
            for parent in rows:
                doc = docs.get(parent.document_id)
                if not doc or parent.version != doc.version:
                    continue
                for child in parent.children:
                    result.append({**child, 'owner_id': user_id, 'parent_id': parent.id,
                        'document_id': doc.id, 'document_version': doc.version,
                        'file_type': doc.file_type, 'source_title': doc.title,
                        'source_reference': doc.filename,
                        'is_active': True, 'updated_at': doc.updated_at.isoformat()})
            return result

    def user_parent(self, user_id, parent_id, version=None):
        """按原文取父块；owner 不匹配按不存在处理，避免跨账号读到别人的文档。"""
        with self.db.transaction() as s:
            row = s.execute(select(UserParentChunk, UserDocument).join(
                UserDocument, UserParentChunk.document_id == UserDocument.id).where(
                UserParentChunk.id == parent_id, UserParentChunk.owner_id == user_id,
                UserParentChunk.is_active.is_(True), UserDocument.is_active.is_(True))).first()
            if not row or row[0].version != row[1].version or (version and version != row[1].version):
                raise NotFound('来源已更新或删除，请重新检索')
            parent, doc = row
            return {'id': parent.id, 'document_id': doc.id, 'owner_id': doc.owner_id,
                    'title': parent.title, 'source_title': doc.title, 'source': doc.filename,
                    'file_type': doc.file_type, 'version': doc.version, 'text': parent.text}

    def set_user_kb_importing(self, user_id, importing):
        with self.db.transaction() as s:
            return self._mark_user_kb(s, user_id, importing=importing)

    def finish_user_kb_reindex(self, user_id, revision, fingerprint):
        with self.db.transaction() as s:
            return self._mark_user_kb(s, user_id, importing=False, index_revision=revision,
                                      fingerprint=fingerprint)

    # ------------------------------------------------------------------ 收藏
    def list_favorites(self, user_id):
        with self.db.transaction() as s:
            rows = s.execute(select(Favorite, Coach).join(Coach, Favorite.coach_id == Coach.id)
                             .where(Favorite.user_id == user_id).order_by(Favorite.created_at.desc())).all()
            return [{'favorited_at': fav.created_at.isoformat() + 'Z', 'coach': asdict(coach)}
                    for fav, coach in rows]

    def add_favorite(self, user_id, coach_id):
        with self.db.transaction() as s:
            if not s.get(Coach, coach_id):
                raise NotFound('教练不存在')
            exists = s.scalars(select(Favorite).where(Favorite.user_id == user_id,
                                                      Favorite.coach_id == coach_id)).first()
            if not exists:
                s.add(Favorite(user_id=user_id, coach_id=coach_id))
            return {'coach_id': coach_id, 'favorite': True}

    def remove_favorite(self, user_id, coach_id):
        with self.db.transaction() as s:
            s.execute(delete(Favorite).where(Favorite.user_id == user_id, Favorite.coach_id == coach_id))
            return {'coach_id': coach_id, 'favorite': False}

    # ------------------------------------------------------------------ 预登记与 4 周表
    def _coach_dict(self, s, coach_id):
        coach = s.get(Coach, coach_id)
        if not coach:
            raise NotFound('教练不存在')
        return asdict(coach)

    def coach_schedule(self, coach_id, user_id=None, weeks=4, today=None):
        """未来 weeks 周的逐日可约视图；已登记格子带上该账号的预登记记录。"""
        with self.db.transaction() as s:
            coach = self._coach_dict(s, coach_id)
            rows = []
            if user_id:
                rows = s.scalars(select(Preregistration).where(Preregistration.user_id == user_id,
                                                               Preregistration.coach_id == coach_id)).all()
            registered = {}
            for record in rows:
                for slot in record.slots:
                    registered.setdefault(slot['date'], []).append({**slot, 'status': record.status,
                        'status_label': PREREG_STATUS_LABEL.get(record.status, record.status),
                        'id': record.id})
            today = hk_today(today)
        days = []
        cursor = today + timedelta(days=1)
        for _ in range(weeks * 7):
            entry = day_view(coach, cursor)
            entry['registrations'] = registered.get(cursor.isoformat(), [])
            days.append(entry)
            cursor += timedelta(days=1)
        return {'coach': {'id': coach['id'], 'name': coach['name'], 'price': coach['price'],
                          'districts': coach['districts'], 'delivery_modes': coach['delivery_modes'],
                          'specialties': coach['specialties'], 'availability': coach['availability'],
                          'is_demo': coach['is_demo']},
                'timezone': 'Asia/Hong_Kong', 'start_date': days[0]['date'], 'end_date': days[-1]['date'],
                'days': days}

    def create_preregistration(self, user_id, coach_id, slots, note='', session_id=None, today=None):
        with self.db.transaction() as s:
            coach = self._coach_dict(s, coach_id)
            cleaned = validate_slots(coach, slots, today)
            record = Preregistration(user_id=user_id, coach_id=coach_id, session_id=session_id,
                                     slots=cleaned, note=sanitize_note(note),
                                     status='pending_coach', updated_at=now())
            s.add(record)
            s.flush()
            return self._preregistration_view(record, coach)

    def _preregistration_view(self, record, coach=None):
        view = asdict(record)
        view['slots'] = record.slots
        view['status_label'] = PREREG_STATUS_LABEL.get(record.status, record.status)
        view['coach'] = ({'id': coach['id'], 'name': coach['name'], 'price': coach['price']}
                         if coach else None)
        return view

    def list_preregistrations(self, user_id, coach_id=None):
        with self.db.transaction() as s:
            query = select(Preregistration, Coach).join(Coach, Preregistration.coach_id == Coach.id).where(
                Preregistration.user_id == user_id)
            if coach_id:
                query = query.where(Preregistration.coach_id == coach_id)
            rows = s.execute(query.order_by(Preregistration.created_at.desc())).all()
            return [self._preregistration_view(record, asdict(coach)) for record, coach in rows]

    def delete_preregistration(self, user_id, prereg_id):
        """删除单条预登记；只能删自己的，越权按不存在处理（不泄露存在性）。"""
        with self.db.transaction() as s:
            row = s.get(Preregistration, prereg_id)
            if not row or row.user_id != user_id:
                raise NotFound('预登记不存在')
            s.delete(row)
            return {'deleted': prereg_id}

    def delete_preregistrations(self, user_id, coach_id=None):
        """清空该账号的全部预登记（可按教练限定）。"""
        with self.db.transaction() as s:
            query = delete(Preregistration).where(Preregistration.user_id == user_id)
            if coach_id:
                query = query.where(Preregistration.coach_id == coach_id)
            result = s.execute(query)
            return {'deleted': result.rowcount or 0}

    # ------------------------------------------------------------------ 跨会话画像
    def compute_profile(self, user_id):
        with self.db.transaction() as s:
            return self._compute_profile(s, user_id)

    def _compute_profile(self, s, user_id, since=None):
        query = (select(RequirementVersion.snapshot, Conversation.id)
                 .join(Run, RequirementVersion.run_id == Run.id)
                 .join(Conversation, Run.session_id == Conversation.id)
                 .where(Conversation.user_id == user_id))
        if since is not None:
            # 清空过画像后，只累积 reset_at 之后的新会话需求。
            query = query.where(RequirementVersion.created_at > since)
        rows = s.execute(query.order_by(RequirementVersion.created_at)).all()
        goals, districts, modes, budgets = Counter(), Counter(), Counter(), []
        preferences, conditions = Counter(), Counter()
        for (snapshot, _) in rows:
            data = snapshot or {}
            if data.get('goal'):
                goals[data['goal']] += 1
            if data.get('district'):
                districts[data['district']] += 1
            if data.get('delivery_mode'):
                modes[data['delivery_mode']] += 1
            if data.get('budget_max') is not None:
                budgets.append(data['budget_max'])
            preferences.update(data.get('preferences') or [])
            conditions.update(data.get('self_reported_conditions') or [])
        # 会话计数与需求版本同源，清空画像后不会出现"目标为空却显示 18 次会话"的矛盾。
        session_count = len({session_id for _, session_id in rows})
        favorites = s.execute(select(Favorite, Coach).join(Coach, Favorite.coach_id == Coach.id)
                              .where(Favorite.user_id == user_id)).all()
        prereg = s.execute(select(Preregistration, Coach).join(Coach, Preregistration.coach_id == Coach.id)
                           .where(Preregistration.user_id == user_id)).all()
        return {
            'goals': [{'value': v, 'count': n} for v, n in goals.most_common()],
            'districts': [{'value': v, 'count': n} for v, n in districts.most_common()],
            'delivery_modes': [{'value': v, 'count': n} for v, n in modes.most_common()],
            'preferences': [{'value': v, 'count': n} for v, n in preferences.most_common(8)],
            'self_reported_conditions': [{'value': v, 'count': n} for v, n in conditions.most_common(8)],
            'budget': {'min': min(budgets), 'max': max(budgets)} if budgets else None,
            'sessions': session_count, 'requirement_versions': len(rows),
            'favorite_coaches': [{'id': coach.id, 'name': coach.name} for _, coach in favorites],
            'preregistered_coaches': [{'id': coach.id, 'name': coach.name} for _, coach in prereg],
        }

    def _profile_row(self, s, user_id):
        return s.scalars(select(UserProfile).where(UserProfile.user_id == user_id)).first()

    def _profile_view(self, row, data):
        return {**data, 'reset_at': row.reset_at.isoformat() + 'Z' if row.reset_at else None,
                'updated_at': row.updated_at.isoformat() + 'Z'}

    def refresh_profile(self, user_id):
        if not user_id:
            return None
        with self.db.transaction() as s:
            row = self._profile_row(s, user_id)
            since = row.reset_at if row else None
            data = self._compute_profile(s, user_id, since)
            if row:
                row.data, row.updated_at = data, now()
            else:
                row = UserProfile(user_id=user_id, data=data, updated_at=now())
                s.add(row)
            return self._profile_view(row, data)

    def reset_profile(self, user_id):
        """清空画像并重置累积：保留聊天记录，只让画像从此刻起重新累积。"""
        with self.db.transaction() as s:
            row = self._profile_row(s, user_id)
            stamp = now()
            empty = empty_profile()
            if row:
                row.reset_at, row.data, row.updated_at = stamp, empty, stamp
            else:
                row = UserProfile(user_id=user_id, data=empty, reset_at=stamp, updated_at=stamp)
                s.add(row)
            return self._profile_view(row, row.data)

    def user_profile(self, user_id):
        """优先返回已缓存画像；没有则即时聚合（不阻塞地确保账号面板始终有内容）。"""
        with self.db.transaction() as s:
            row = self._profile_row(s, user_id)
            if row:
                return self._profile_view(row, row.data)
        return self.refresh_profile(user_id)

    def profile_context(self, user_id):
        """给路由/知识回答的软背景：只描述已知偏好，不是硬条件。"""
        if not user_id:
            return {}
        profile = self.user_profile(user_id)
        return {'known_goals': [g['value'] for g in profile.get('goals', [])][:3],
                'known_districts': [d['value'] for d in profile.get('districts', [])][:3],
                'known_preferences': [p['value'] for p in profile.get('preferences', [])][:5],
                'known_conditions': [c['value'] for c in profile.get('self_reported_conditions', [])][:3],
                'budget_history': profile.get('budget')}

    # ------------------------------------------------------------------ 决策报告
    def run_report(self, run_id, user_id=None):
        with self.db.transaction() as s:
            row = s.get(Run, run_id)
            if not row:
                raise NotFound('运行不存在')
            if user_id is not None:
                self._session_owned(s, row.session_id, user_id)
            run = self._run_view(s, row)
            conversation = s.get(Conversation, row.session_id)
            user = s.get(User, conversation.user_id) if conversation and conversation.user_id else None
            username = user.username if user else None
        result = run.get('result') or {}
        if not result:
            raise Conflict('该任务还没有可导出的结果')
        knowledge = result.get('knowledge') or {}
        evidence_refs = []
        seen = set()

        def add_ref(item, source_kind):
            key = item.get('id')
            if key in seen:
                return
            seen.add(key)
            evidence_refs.append({'id': key, 'title': item.get('title'), 'source_title': item.get('source_title'),
                                  'source': item.get('source'), 'version': item.get('version'),
                                  'coach_id': item.get('coach_id'), 'kind': source_kind})

        for item in result.get('evidence') or []:
            add_ref(item, 'coach')
        for item in knowledge.get('evidence') or []:
            add_ref(item, 'knowledge')
        candidates = []
        for c in result.get('candidates') or []:
            reasons = []
            for reason in c.get('reasons') or []:
                ref = next((e for e in evidence_refs if e['id'] == reason.get('evidence_id')), None)
                reasons.append({'text': reason.get('text'), 'quote': reason.get('quote'),
                                'evidence_id': reason.get('evidence_id'),
                                'source_title': (ref or {}).get('source_title'),
                                'source': (ref or {}).get('source')})
            candidates.append({'id': c.get('id'), 'name': c.get('name'), 'price': c.get('price'),
                               'districts': c.get('districts'), 'delivery_modes': c.get('delivery_modes'),
                               'specialties': c.get('specialties'), 'is_demo': c.get('is_demo'),
                               'availability': c.get('availability'),
                               'score': c.get('score'), 'score_breakdown': c.get('score_breakdown'),
                               'score_label': c.get('score_label') or '排序参考分（非成功概率）',
                               'hard_matches': c.get('hard_matches'), 'reasons': reasons,
                               'pending': c.get('pending')})
        req = run.get('requirements') or {}
        return {'run_id': run_id, 'session_id': run['session_id'], 'username': username,
                'generated_at': datetime.now(HK).isoformat(), 'mode': result.get('mode'),
                'status': run['status'], 'message': run['message'], 'version': run.get('version'),
                'intents': result.get('intents'), 'summary': result.get('summary'),
                'notice': result.get('notice'), 'degraded': result.get('degraded') or [],
                'knowledge_version': result.get('knowledge_version'),
                'requirements': {'goal': req.get('goal'), 'budget_max': req.get('budget_max'),
                                 'district': req.get('district'), 'delivery_mode': req.get('delivery_mode'),
                                 'available_windows': req.get('available_windows') or [],
                                 'preferences': req.get('preferences') or [],
                                 'self_reported_conditions': req.get('self_reported_conditions') or []},
                'candidates': candidates,
                'knowledge': ({'answer': knowledge.get('answer'), 'source_type': knowledge.get('source_type'),
                               'citations': knowledge.get('citations') or []} if knowledge else None),
                'preregistration': result.get('preregistration'),
                'evidence': evidence_refs}
