import pytest
from sqlalchemy import select
from app.db import Document, ParentChunk
from app.ingestion import import_sql, load_dataset, DocumentRecord, Dataset
from app.retrieval import rrf
from app.repository import NotFound


async def test_original_rewrite_rrf_rerank_parent_scoping(components):
    _, repo, _, _, retrieval, _ = components
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    result = await retrieval.retrieve('耐心减脂教练', '新手 膝盖不适 低冲击 耐心 减脂', ['coach-01'], {},
                                       {'version': 1}, emit)
    assert result['metrics']['channels'] == 4
    assert result['metrics']['parents'] > 0
    assert all(e['coach_id'] == 'coach-01' for e in result['evidence'])
    assert all(repo.parent(e['id'])['text'].startswith(e['text']) for e in result['evidence'])
    assert len({e['id'] for e in result['evidence']}) == len(result['evidence'])
    original = next(d for kind, d in events if kind == 'recall')['queries'][0]
    assert original == '耐心减脂教练'


def test_rrf_uses_rank_not_raw_scores():
    a = {'chunk_id': 'a', 'channel_score': .01}
    b = {'chunk_id': 'b', 'channel_score': 1000}
    result = rrf([[a, b], [a]])
    assert result[0]['chunk_id'] == 'a'
    assert result[0]['rrf_score'] == pytest.approx(2 / 61)
    assert rrf([[a, a]])[0]['rrf_score'] == pytest.approx(1 / 61)


async def test_document_update_delete_and_cache_invalidation(components):
    _, repo, _, cache, retrieval, _ = components
    async def emit(*args):
        pass
    before = await retrieval.retrieve('耐心', '耐心', ['coach-01'], {}, {'version': 1}, emit)
    old_ids = {e['id'] for e in before['evidence']}
    doc = DocumentRecord(id='coach-01-teaching', coach_id='coach-01', title='更新资料',
                         source='demo://updated', document_type='teaching', version='2',
                         content='# 新说明\n\n教学动作讲解已更新，资料版本二。', is_demo=True)
    import_sql(repo, Dataset(coaches=[], documents=[doc]))
    after = await retrieval.retrieve('耐心', '耐心', ['coach-01'], {}, {'version': 1}, emit)
    assert after['knowledge_version'] > before['knowledge_version']
    assert all(e['version'] == '2' for e in after['evidence'] if e['document_id'] == doc.id)
    assert old_ids - {e['id'] for e in after['evidence']}
    with repo.db.transaction() as s:
        active_ids = [p.id for p in s.scalars(select(ParentChunk).where(ParentChunk.document_id == doc.id, ParentChunk.is_active.is_(True)))]
    import_sql(repo, Dataset(coaches=[], documents=[]), replace=True)
    for parent_id in active_ids:
        with pytest.raises(NotFound):
            repo.parent(parent_id)
    deleted = await retrieval.retrieve('耐心', '耐心', ['coach-01'], {}, {'version': 1}, emit)
    assert deleted['evidence'] == []


def test_seed_is_idempotent_and_version_required(components):
    from pathlib import Path
    _, repo, _, _, _, _ = components
    path = Path(__file__).resolve().parents[2] / 'data/demo/dataset.json'
    dataset = load_dataset(path)
    state = repo.knowledge_state()
    result = import_sql(repo, dataset)
    assert not result['changed'] and result['revision'] == state['revision']
    dataset.documents[0].content += '\n内容变化'
    with pytest.raises(ValueError, match='version'):
        import_sql(repo, dataset)


async def test_cache_includes_versions_and_coach_scope(components):
    _, repo, _, cache, retrieval, _ = components
    async def emit(*args):
        pass
    for ids, version in [(['coach-01'], 1), (['coach-02'], 1), (['coach-01'], 2)]:
        await retrieval.retrieve('减脂', '减脂', ids, {}, {'version': version}, emit)
    assert len(cache.data) == 3
