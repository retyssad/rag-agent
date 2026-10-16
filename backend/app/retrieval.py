import asyncio
import hashlib
import json
import math
from collections import Counter, defaultdict
from app.observability import get_logger, named_stage
from app.providers import tokens, demo_vector
from app.repository import NotFound

logger = get_logger('app.retrieval')


def rrf(rankings, k=60):
    scores = defaultdict(float)
    rows = {}
    for ranking in rankings:
        seen = set()
        for rank, row in enumerate(ranking, 1):
            key = row['chunk_id']
            if key in seen:
                continue
            seen.add(key)
            rows[key] = row
            scores[key] += 1 / (k + rank)
    return [{**rows[key], 'rrf_score': score} for key, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def fingerprint(settings):
    return f'{settings.bailian_embedding_model}:{settings.embedding_dimension}:{settings.milvus_collection}:chinese:v1'


class MilvusIndex:
    def __init__(self, settings):
        self.settings = settings
        self._client = None

    @property
    def client(self):
        if not self._client:
            from pymilvus import MilvusClient
            self._client = MilvusClient(uri=self.settings.milvus_uri, token=self.settings.milvus_token, timeout=20)
        return self._client

    def ensure_collection(self):
        from pymilvus import DataType, Function, FunctionType
        name = self.settings.milvus_collection
        if self.client.has_collection(name):
            description = self.client.describe_collection(name)
            vector = next(f for f in description['fields'] if f['name'] == 'dense_vector')
            if int(vector['params']['dim']) != self.settings.embedding_dimension:
                raise RuntimeError('Milvus 向量维度不匹配，请使用新集合并重新索引')
            return
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field('chunk_id', DataType.VARCHAR, is_primary=True, max_length=64)
        for field, length in [('coach_id', 64), ('document_id', 64), ('parent_id', 64), ('document_version', 64),
                              ('document_type', 64), ('source_title', 1024), ('source_reference', 2048), ('updated_at', 64)]:
            schema.add_field(field, DataType.VARCHAR, max_length=length)
        schema.add_field('text', DataType.VARCHAR, max_length=8000, enable_analyzer=True,
                         analyzer_params={'type': 'chinese'})
        schema.add_field('is_active', DataType.BOOL)
        schema.add_field('dense_vector', DataType.FLOAT_VECTOR, dim=self.settings.embedding_dimension)
        schema.add_field('sparse_vector', DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(Function(name='text_bm25', function_type=FunctionType.BM25,
                            input_field_names=['text'], output_field_names=['sparse_vector']))
        indexes = self.client.prepare_index_params()
        indexes.add_index('dense_vector', index_type='AUTOINDEX', metric_type='COSINE')
        indexes.add_index('sparse_vector', index_type='SPARSE_INVERTED_INDEX', metric_type='BM25',
                          params={'inverted_index_algo': 'DAAT_MAXSCORE'})
        self.client.create_collection(name, schema=schema, index_params=indexes, consistency_level='Strong')

    def replace_all(self, children, vectors):
        self.ensure_collection()
        name = self.settings.milvus_collection
        # 索引同步期间 SQL importing 标记阻止真实检索，完成后才推进 index_revision。
        self.client.delete(name, filter='chunk_id != ""')
        for start in range(0, len(children), 100):
            rows = [{**c, 'dense_vector': v} for c, v in zip(children[start:start + 100], vectors[start:start + 100])]
            if rows:
                self.client.upsert(name, rows)
        self.client.flush(name)
        self.client.load_collection(name)

    def search(self, query, vector, coach_ids, filters, top_k):
        name = self.settings.milvus_collection
        expression = 'is_active == true'
        params = {}
        if coach_ids is not None:
            if not coach_ids:
                return [], []
            expression += ' and coach_id in {coach_ids}'
            params['coach_ids'] = coach_ids
        for key, value in filters.items():
            if key != 'document_type':
                raise ValueError('仅允许 document_type 检索过滤')
            expression += ' and document_type == {doc_type}'
            params['doc_type'] = value
        fields = ['chunk_id', 'coach_id', 'document_id', 'parent_id', 'text', 'document_version',
                  'source_title', 'source_reference', 'document_type']
        outputs = []
        for field, data, metric in [('dense_vector', vector, 'COSINE'), ('sparse_vector', query, 'BM25')]:
            result = self.client.search(name, data=[data], anns_field=field, filter=expression,
                filter_params=params, limit=top_k, output_fields=fields, search_params={'metric_type': metric})
            outputs.append([{**hit['entity'], 'chunk_id': str(hit['entity']['chunk_id']), 'channel_score': hit['distance']} for hit in result[0]])
        return tuple(outputs)


def demo_search(children, query, vector, top_k):
    q = tokens(query)
    docs = [tokens(row['text']) for row in children]
    avg = sum(len(d) for d in docs) / (len(docs) or 1)
    freq = Counter(t for d in docs for t in set(d))
    dense, sparse = [], []
    for row, doc in zip(children, docs):
        dense_score = sum(a * b for a, b in zip(vector, demo_vector(row['text'])))
        counts = Counter(doc)
        bm25 = sum(math.log(1 + (len(docs) - freq[t] + .5) / (freq[t] + .5)) *
                   (counts[t] * 2.5 / (counts[t] + 1.5 * (.25 + .75 * len(doc) / (avg or 1))))
                   for t in set(q) if counts[t])
        dense.append({**row, 'channel_score': dense_score})
        if bm25 > 0:
            sparse.append({**row, 'channel_score': bm25})
    return tuple(sorted(rows, key=lambda r: (-r['channel_score'], r['chunk_id']))[:top_k] for rows in (dense, sparse))


class Retrieval:
    def __init__(self, settings, repo, providers, cache):
        self.settings, self.repo, self.providers, self.cache = settings, repo, providers, cache
        self.index = MilvusIndex(settings)

    async def retrieve(self, query, rewritten, coach_ids, filters, requirements, emit):
        settings = self.settings
        state = self.repo.knowledge_state()
        if state['importing']:
            raise RuntimeError('知识库正在同步，请稍后重试')
        if settings.app_mode == 'live' and (state['revision'] != state['index_revision'] or
                                           state['index_fingerprint'] != fingerprint(settings)):
            raise RuntimeError('知识索引未同步或模型配置已变更，请执行 import_data --reindex')
        key_data = {'q': query, 'rewritten': rewritten, 'ids': sorted(coach_ids) if coach_ids is not None else None,
                    'filters': filters, 'requirements': requirements, 'knowledge': state['revision'],
                    'embedding': fingerprint(settings), 'rerank': settings.bailian_rerank_model,
                    'mode': settings.app_mode, 'top_k': settings.retrieval_top_k, 'rrf_k': settings.rrf_k,
                    'top_n': settings.rerank_top_n, 'parents': settings.max_parents,
                    'context_chars': settings.max_context_chars}
        key = 'rag:' + hashlib.sha256(json.dumps(key_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cached = await self.cache.get(key)
        if cached:
            logger.info('命中检索缓存', extra={'event': 'retrieval.cache_hit', 'mode': settings.app_mode})
            await emit('retrieval_cache', {'message': '已读取相同需求的有效资料缓存'})
            return cached
        logger.info('检索缓存未命中，开始多路召回', extra={'event': 'retrieval.cache_miss', 'mode': settings.app_mode})
        queries = list(dict.fromkeys([query, rewritten]))
        vectors = await self.providers.embed(queries)
        children = self.repo.active_children(coach_ids, filters) if settings.app_mode == 'demo' else []

        async def recall(q, v):
            if settings.app_mode == 'demo':
                return await asyncio.to_thread(demo_search, children, q, v, settings.retrieval_top_k)
            return await asyncio.to_thread(self.index.search, q, v, coach_ids, filters, settings.retrieval_top_k)

        with named_stage('retrieval:recall'):
            recalled = await asyncio.gather(*(recall(q, v) for q, v in zip(queries, vectors)))
        rankings = [channel for result in recalled for channel in result]
        await emit('recall', {'message': '已检索相关资料', 'queries': queries,
            'channels': [{'query': q, 'dense': len(result[0]), 'bm25': len(result[1])} for q, result in zip(queries, recalled)],
            'mode': settings.app_mode})
        fused = rrf(rankings, settings.rrf_k)
        # 以 SQL 当前版本再次过滤，防止旧索引或并发下架证据返回。
        valid = []
        for chunk in fused:
            try:
                parent = self.repo.parent(chunk['parent_id'], chunk['document_version'])
                if coach_ids is not None and (parent['coach_id'] or '') not in coach_ids:
                    continue
                valid.append((chunk, parent))
            except NotFound:
                continue
        with named_stage('retrieval:rerank'):
            ranked = await self.providers.rerank(query, [c['text'] for c, _ in valid], settings.rerank_top_n)
        evidence, seen, length = [], set(), 0
        for index, score in ranked:
            chunk, parent = valid[index]
            if parent['id'] in seen or len(evidence) >= settings.max_parents:
                continue
            remaining = settings.max_context_chars - length
            if remaining <= 0:
                break
            seen.add(parent['id'])
            text = parent['text'][:remaining]
            length += len(text)
            evidence.append({**parent, 'text': text, 'chunk_id': chunk['chunk_id'],
                             'rrf_score': chunk['rrf_score'], 'rerank_score': score,
                             'truncated': len(text) < len(parent['text'])})
        result = {'evidence': evidence, 'knowledge_version': state['revision'],
                  'metrics': {'channels': len(rankings), 'fused': len(fused), 'reranked': len(ranked), 'parents': len(evidence)}}
        # 若导入期间发生变化，整次结果作废而非缓存部分旧证据。
        end_state = self.repo.knowledge_state()
        if end_state['revision'] != state['revision'] or end_state['importing']:
            raise RuntimeError('资料在查询期间发生更新，请重新发送消息')
        await emit('rerank', {'message': '已核实来源并整理原文', **result['metrics'],
                            'provider': '模拟词项精排' if settings.app_mode == 'demo' else settings.bailian_rerank_model})
        logger.info('检索完成', extra={'event': 'retrieval.done', **result['metrics'],
            'mode': settings.app_mode, 'rerank_provider': settings.bailian_rerank_model})
        await self.cache.set(key, result)
        return result
