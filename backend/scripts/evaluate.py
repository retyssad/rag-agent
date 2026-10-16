"""固定小型评估集；默认仅评估 demo，不以其指标证明真实模型质量。"""
import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from app.config import Settings
from app.db import Database, Base
from app.observability import configure_logging
from app.repository import Repository
from app.ingestion import load_dataset, import_sql
from app.providers import Providers
from app.retrieval import Retrieval
from app.agents import Orchestrator


class EvaluationCache:
    degraded = False
    async def call(self, *args, **kwargs):
        return None
    async def get(self, *args):
        return None
    async def set(self, *args, **kwargs):
        pass


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='docs/evaluation-demo.json')
    parser.add_argument('--live', action='store_true', help='调用真实模型并使用当前数据库，产生 API 费用')
    parser.add_argument('--case', help='仅重跑指定业务用例 ID（其余结果可与现有报告合并）')
    parser.add_argument('--merge-report', action='store_true', help='保留现有报告中本次未重跑用例的实测结果')
    parser.add_argument('--session-id', help='针对已存在会话运行指定多轮用例的最后一条消息，使用当前持久化历史')
    args = parser.parse_args()
    cases = json.loads(Path('data/evaluation/cases.json').read_text(encoding='utf-8'))
    existing = {}
    if args.merge_report and Path(args.output).exists():
        existing = json.loads(Path(args.output).read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='coach-eval-') as temporary:
        settings = Settings() if args.live else Settings(_env_file=None, app_mode='demo', database_url=f'sqlite:///{Path(temporary) / "eval.db"}')
        if args.live:
            if settings.app_mode != 'live':
                raise RuntimeError('请先配置 APP_MODE=live')
            settings.require_live_keys()
        # 评估进度与报告走 stdout，JSON 日志走 stderr，避免两者混淆。
        configure_logging(settings, stream=sys.stderr)
        db = Database(settings.database_url)
        repo = Repository(db)
        if not args.live:
            Base.metadata.create_all(db.engine)
            import_sql(repo, load_dataset('data/demo/dataset.json'))
        providers = Providers(settings)
        retrieval = Retrieval(settings, repo, providers, EvaluationCache())
        agent = Orchestrator(settings, repo, providers, retrieval, EvaluationCache())
        business, recall = [], []
        try:
            for case in cases['business']:
                if args.case and case['id'] != args.case:
                    continue
                session_id = args.session_id or repo.create_session()['id']
                final = None
                messages = case['messages'][-1:] if args.session_id else case['messages']
                for message in messages:
                    print(json.dumps({'evaluation_case':case['id'],'phase':'running'},ensure_ascii=False), flush=True)
                    run = repo.create_run(session_id, message, execution_scope='cli')
                    await agent.execute(run['id'])
                    final = repo.run(run['id'])
                result = final.get('result') or {}
                intents = result.get('intents', final['context'].get('intents', []))
                violations = sum(not all(c['hard_matches'].values()) for c in result.get('candidates', []))
                supported = all(repo.parent(reason['evidence_id'])['coach_id'] == c['id'] and
                                reason['quote'] in repo.parent(reason['evidence_id'])['text']
                                for c in result.get('candidates', []) for reason in c['reasons'])
                expected_patch = case.get('expected_requirements', {})
                requirements_ok = all(final['requirements'].get(k) == v for k, v in expected_patch.items())
                passed = (final['status'] == case['status'] and case['intent'] in intents and
                          violations == 0 and supported and requirements_ok)
                business.append({'id': case['id'], 'status': final['status'], 'intents': intents,
                    'hard_condition_violations': violations, 'evidence_valid': supported,
                    'requirements_valid': requirements_ok, 'passed': passed,
                    'run_id': final['id'], 'session_id': session_id,
                    'measured_at': datetime.now(ZoneInfo('Asia/Hong_Kong')).isoformat()})
                print(json.dumps({'evaluation_case':case['id'],'passed':passed,'status':final['status']},ensure_ascii=False),flush=True)
            async def emit(*args):
                pass
            for case in cases['retrieval']:
                result = await retrieval.retrieve(case['query'], case['rewrite'], case['coach_ids'], {}, {'version': 1}, emit)
                docs = list(dict.fromkeys(e['document_id'] for e in result['evidence']))[:5]
                expected = set(case['relevant_documents'])
                hits = [i + 1 for i, doc in enumerate(docs) if doc in expected]
                recall.append({'id': case['id'], 'returned_documents': docs,
                    'recall_at_5': len(expected & set(docs)) / len(expected),
                    'reciprocal_rank': 1 / min(hits) if hits else 0})
            if existing:
                measured = {c['id']:c for c in existing.get('business_cases', [])}
                measured.update({c['id']:c for c in business})
                business = list(measured.values())
            report = {'mode': settings.app_mode, 'timestamp': datetime.now(ZoneInfo('Asia/Hong_Kong')).isoformat(),
                'scope': '固定演示集，用于回归验证；不代表真实教练/模型推荐效果',
                'business_pass_rate': sum(row['passed'] for row in business) / len(business),
                'retrieval_recall_at_5': sum(row['recall_at_5'] for row in recall) / len(recall),
                'retrieval_mrr': sum(row['reciprocal_rank'] for row in recall) / len(recall),
                'business_cases': business, 'retrieval_cases': recall}
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: v for k, v in report.items() if not k.endswith('_cases')}, ensure_ascii=False))
            if not all(row['passed'] for row in business):
                raise RuntimeError('部分业务评估未通过，请查看输出报告')
        finally:
            await providers.close()
            db.engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
