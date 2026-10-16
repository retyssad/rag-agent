import asyncio
import hashlib
import json
import re
from pathlib import Path
from pydantic import Field, field_validator
from sqlalchemy import select, update
from app.db import Coach, Document, ParentChunk, KnowledgeState, now
from app.observability import get_logger
from app.schemas import StrictModel, TimeWindow
from app.retrieval import fingerprint

logger = get_logger('app.ingestion')


class CoachRecord(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0)
    districts: list[str] = Field(min_length=1)
    delivery_modes: list[str] = Field(min_length=1)
    specialties: list[str]
    qualifications: list[dict] = Field(default_factory=list)
    courses: list[dict] = Field(default_factory=list)
    availability: list[TimeWindow]
    bio: str = ''
    is_demo: bool = False

    @field_validator('delivery_modes')
    @classmethod
    def mode(cls, values):
        if any(v not in ('线上', '线下') for v in values):
            raise ValueError('delivery_modes 只能包含线上/线下')
        return values


class DocumentRecord(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    coach_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1, max_length=500)
    document_type: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=500000)
    is_demo: bool = False


class Dataset(StrictModel):
    coaches: list[CoachRecord]
    documents: list[DocumentRecord]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def split_document(doc):
    # 标题/段落构成父块；超长章节按段落拆到1800字，子块400字、重叠60字。
    sections = re.split(r'(?m)(?=^#{1,3}\s)', doc.content)
    parents = []
    for section in sections:
        if not section.strip():
            continue
        pieces, current = [], ''
        for paragraph in re.split(r'\n\s*\n', section.strip()):
            for start in range(0, len(paragraph), 1800):
                part = paragraph[start:start + 1800]
                if len(current) + len(part) + 2 > 1800 and current:
                    pieces.append(current)
                    current = ''
                current += ('\n\n' if current else '') + part
        if current:
            pieces.append(current)
        for text in pieces:
            parent_id = digest(f'{doc.id}:{doc.version}:{len(parents)}:{text}')[:64]
            children = [{'chunk_id': digest(f'{parent_id}:{start}'), 'text': text[start:start + 400]}
                        for start in range(0, len(text), 340)]
            parents.append({'id': parent_id, 'document_id': doc.id, 'title': text.split('\n')[0].lstrip('# ').strip()[:255],
                            'text': text, 'children': children, 'version': doc.version, 'is_active': True})
    return parents


def load_dataset(path):
    return Dataset.model_validate_json(Path(path).read_text(encoding='utf-8-sig'))


def import_sql(repo, dataset, replace=False):
    if len({c.id for c in dataset.coaches}) != len(dataset.coaches) or len({d.id for d in dataset.documents}) != len(dataset.documents):
        raise ValueError('同一文件不能包含重复教练/文档 ID')
    with repo.db.transaction() as s:
        state = s.get(KnowledgeState, 1)
        if not state:
            state = KnowledgeState(id=1, revision=0, index_revision=0, index_fingerprint='', importing=False)
            s.add(state)
            s.flush()
        if state.importing:
            raise RuntimeError('索引导入未结束；确认没有其他导入进程后执行 --reindex 恢复')
        changed = False
        for record in dataset.coaches:
            old = s.get(Coach, record.id)
            values = record.model_dump()
            if old:
                if any(getattr(old, k) != v for k, v in values.items()):
                    for k, v in values.items():
                        setattr(old, k, v)
                    old.updated_at = now()
                    changed = True
            else:
                s.add(Coach(**values))
                changed = True
        s.flush()
        for record in dataset.documents:
            if record.coach_id and not s.get(Coach, record.coach_id):
                raise ValueError(f'文档 {record.id} 引用了不存在的教练 {record.coach_id}')
            old = s.get(Document, record.id)
            content_hash = digest(json.dumps(record.model_dump(), sort_keys=True, ensure_ascii=False))
            if old and old.content_hash == content_hash and old.is_active:
                continue
            if old and old.version == record.version and old.content_hash != content_hash:
                raise ValueError(f'文档 {record.id} 内容变化时必须更新 version')
            s.execute(update(ParentChunk).where(ParentChunk.document_id == record.id).values(is_active=False))
            values = {**record.model_dump(), 'content_hash': content_hash, 'is_active': True, 'updated_at': now()}
            if old:
                for k, v in values.items():
                    setattr(old, k, v)
            else:
                s.add(Document(**values))
            s.flush()
            for parent in split_document(record):
                s.merge(ParentChunk(**parent))
            changed = True
        if replace:
            keep = {d.id for d in dataset.documents}
            for doc in s.scalars(select(Document).where(Document.is_active.is_(True))):
                if doc.id not in keep:
                    doc.is_active = False
                    s.execute(update(ParentChunk).where(ParentChunk.document_id == doc.id).values(is_active=False))
                    changed = True
        if changed:
            state.revision += 1
        summary = {'changed': changed, 'revision': state.revision, 'coaches': len(dataset.coaches),
                   'documents': len(dataset.documents)}
        logger.info('数据导入完成', extra={'event': 'ingestion.import', **summary})
        return summary


async def synchronize_index(settings, repo, providers, retrieval):
    with repo.db.transaction() as s:
        state = s.get(KnowledgeState, 1)
        if not state:
            raise RuntimeError('请先导入数据')
        state.importing = True
        revision = state.revision
    # 即使外部失败也保留 importing=True，防止查询不完整索引；--reindex 可以修复。
    children = repo.active_children()
    if settings.app_mode == 'live':
        vectors = await providers.embed([c['text'] for c in children])
        await asyncio.to_thread(retrieval.index.replace_all, children, vectors)
    with repo.db.transaction() as s:
        state = s.get(KnowledgeState, 1)
        if state.revision != revision:
            raise RuntimeError('导入期间资料变动，请重新索引')
        state.importing = False
        state.index_revision = revision
        state.index_fingerprint = fingerprint(settings) if settings.app_mode == 'live' else 'demo'
    summary = {'revision': revision, 'chunks': len(children), 'mode': settings.app_mode}
    logger.info('索引同步完成', extra={'event': 'ingestion.index', **summary})
    return summary
