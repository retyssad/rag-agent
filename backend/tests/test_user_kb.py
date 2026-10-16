"""用户专属知识库：导入、隔离、带引用问答。"""
from pathlib import Path
import asyncio
import httpx
import pytest
from app.auth import AuthService
from app.config import Settings
from app.db import Base
from app.documents import ExtractError, extract
from app.ingestion import import_sql, load_dataset
from app.main import create_app
from app.repository import Repository
from app.userkb import split_user_document

DATASET = Path(__file__).resolve().parents[2] / 'data/demo/dataset.json'
SAMPLES = Path(__file__).resolve().parents[2] / 'data/kb-samples'


def make_user(repo, username='kb-user'):
    return AuthService(repo.db).register(username, 'password123')[0]


def read(sample):
    return (SAMPLES / sample).read_bytes()


async def test_extract_supports_markdown_pdf_and_rejects_bad_input():
    text, meta = extract('protein-intake.md', read('protein-intake.md'))
    assert meta['file_type'] == 'md' and '蛋白质' in text
    pdf_text, pdf_meta = extract('hydration.pdf', read('hydration.pdf'))
    assert pdf_meta['file_type'] == 'pdf' and '补水' in pdf_text
    with pytest.raises(ExtractError):
        extract('photo.png', b'\x89PNG\r\n')
    with pytest.raises(ExtractError):
        extract('empty.md', b'   \n  ')
    # 纯文本按 UTF-8 / GB18030 解码
    assert extract('note.txt', '肌酸 每天 5 克'.encode('utf-8'))[0] == '肌酸 每天 5 克'
    assert '肌酸' in extract('note.txt', '肌酸'.encode('gb18030'))[0]


async def test_split_makes_parents_and_children():
    parents = split_user_document(read('creatine-and-supplements.md').decode('utf-8'))
    assert parents and all(p['children'] and len(p['text']) <= 1800 for p in parents)


def test_live_answer_dependencies_are_importable():
    """真实模式问答分支的导入必须在模块层面成立（demo 模式不会走到，容易漏测）。"""
    from app.agents import KnowledgeAnswer, Rewrites, strip_inline_ids
    assert callable(strip_inline_ids) and KnowledgeAnswer and Rewrites


async def test_ingest_and_query_use_only_own_documents(components):
    """A 上传的文档只在 A 的知识库里可检索；B 无论问答还是直接取原文都拿不到。"""
    settings, repo, providers, cache, _, _ = components
    from app.userkb import UserKnowledge
    user_kb = UserKnowledge(settings, repo, providers, cache)
    a = make_user(repo, 'kbalice')
    b = make_user(repo, 'kbbob')
    text = read('creatine-and-supplements.md').decode('utf-8')
    await user_kb.ingest(a['id'], title='肌酸与补剂', filename='creatine-and-supplements.md',
                         file_type='md', byte_size=len(text), text=text, content_hash='hash-a')
    assert [d['title'] for d in repo.list_user_documents(a['id'])] == ['肌酸与补剂']
    assert repo.list_user_documents(b['id']) == []
    # A 能问到肌酸
    answer = await user_kb.answer(a['id'], '一水肌酸冲击法和维持量分别是多少？')
    assert answer['source_type'] == 'user_documents' and answer['citations']
    assert all(e['owner_id'] == a['id'] for e in answer['evidence'])
    # B 的知识库为空 → 明确说明没有文档，且不返回任何 A 的原文
    empty = await user_kb.answer(b['id'], '一水肌酸怎么吃？')
    assert empty['citations'] == [] and empty['evidence'] == []
    assert '还没有文档' in empty['answer']
    # 直接按 A 的父块 ID 取原文，B 一律 404（不泄露存在性）
    parent_id = answer['evidence'][0]['id']
    with pytest.raises(Exception):
        repo.user_parent(b['id'], parent_id)
    assert repo.user_parent(a['id'], parent_id)['text']


async def test_delete_document_removes_its_parents(components):
    settings, repo, _, _, _, _ = components
    from app.userkb import UserKnowledge
    user_kb = UserKnowledge(settings, repo, components[2], components[3])
    user = make_user(repo, 'kbdel')
    text = read('protein-intake.md').decode('utf-8')
    result = await user_kb.ingest(user['id'], title='蛋白', filename='protein-intake.md',
                                  file_type='md', byte_size=len(text), text=text, content_hash='h1')
    doc_id = result['document']['id']
    assert repo.active_user_children(user['id'])
    await user_kb.delete_document(user['id'], doc_id)
    assert repo.list_user_documents(user['id']) == []
    assert repo.active_user_children(user['id']) == []
    answer = await user_kb.answer(user['id'], '每天需要多少蛋白质？')
    assert answer['citations'] == []


def test_child_fields_cover_expected_milvus_schema(components):
    """Milvus upsert 要求每个字段都出现（缺一个就报 DataNotMatchException）。

    这里不连 Milvus，只校验子块字段集覆盖集合 schema 声明的全部非向量字段；
    真实 Milvus 的 upsert 由集成测试覆盖。
    """
    _, repo, _, _, _, _ = components
    user = make_user(repo, 'kbschema')
    text = read('hydration.pdf')
    content, meta = extract('hydration.pdf', text)
    repo.add_user_document(user['id'], title='补水', filename='hydration.pdf',
                           file_type=meta['file_type'], byte_size=len(text),
                           content=content, content_hash='h')
    doc = repo.list_user_documents(user['id'])[0]
    repo.add_user_parents(user['id'], doc['id'], split_user_document(content))
    children = repo.active_user_children(user['id'])
    assert children
    required = {'chunk_id', 'owner_id', 'document_id', 'parent_id', 'document_version',
                'file_type', 'source_title', 'source_reference', 'updated_at', 'text', 'is_active'}
    missing = required - set(children[0].keys())
    assert not missing, f'子块缺少 Milvus 必需字段：{missing}'


async def test_kb_endpoints_upload_query_and_isolation(tmp_path):
    app = create_app(Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "kb.db"}',
                              redis_url='redis://127.0.0.1:1/0', app_mode='demo'))
    repo = app.state.repo
    Base.metadata.create_all(repo.db.engine)
    import_sql(repo, load_dataset(DATASET))
    from conftest import MemoryCache
    app.state.orchestrator.cache = app.state.retrieval.cache = MemoryCache()
    app.state.user_kb.cache = MemoryCache()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as a, \
                   httpx.AsyncClient(transport=transport, base_url='http://test') as b:
            assert (await a.post('/api/auth/register', json={'username': 'alice', 'password': 'password123'})).status_code == 201
            assert (await b.post('/api/auth/register', json={'username': 'bob', 'password': 'password123'})).status_code == 201
            # 上传 Markdown
            upload = await a.post('/api/kb/documents',
                files={'file': ('protein-intake.md', read('protein-intake.md'), 'text/markdown')})
            assert upload.status_code == 201, upload.text
            assert upload.json()['document']['title'].endswith('.md')
            # 上传 PDF
            pdf = await a.post('/api/kb/documents',
                files={'file': ('hydration.pdf', read('hydration.pdf'), 'application/pdf')})
            assert pdf.status_code == 201, pdf.text
            docs = (await a.get('/api/kb/documents')).json()
            assert len(docs['documents']) == 2 and docs['state']['revision'] >= 2
            # 非法类型 400
            bad = await a.post('/api/kb/documents', files={'file': ('x.exe', b'MZ', 'application/octet-stream')})
            assert bad.status_code == 400
            # A 提问命中自己的文档并带引用
            asked = await a.post('/api/kb/query', json={'question': '训练时应该怎么补水？'})
            assert asked.status_code == 200
            body = asked.json()
            assert body['citations'] and body['evidence']
            # 引文原文可打开（A）
            parent = body['evidence'][0]['id']
            ok = await a.get(f'/api/kb/evidence/{parent}')
            assert ok.status_code == 200 and ok.json()['text']
            # B 看不到 A 的文档、问不到、也打不开原文
            assert (await b.get('/api/kb/documents')).json()['documents'] == []
            b_ask = await b.post('/api/kb/query', json={'question': '训练时应该怎么补水？'})
            assert b_ask.status_code == 200 and b_ask.json()['citations'] == []
            assert (await b.get(f'/api/kb/evidence/{parent}')).status_code == 404
            assert (await b.delete(f'/api/kb/documents/{docs["documents"][0]["id"]}')).status_code == 404
