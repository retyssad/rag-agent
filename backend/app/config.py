from functools import lru_cache
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore', case_sensitive=False)
    app_mode: Literal['demo', 'live'] = 'demo'
    database_url: str = 'sqlite:///./.local/coach.db'
    redis_url: str = 'redis://127.0.0.1:6379/0'
    cors_origins: str = 'http://localhost:5173,http://127.0.0.1:5173'
    chat_api_key: str = ''
    chat_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    chat_model: str = 'qwen3.8-flash'
    dashscope_api_key: str = ''
    bailian_embedding_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings'
    bailian_embedding_model: str = 'text-embedding-v4'
    embedding_dimension: int = Field(1024, gt=0)
    bailian_rerank_url: str = 'https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank'
    bailian_rerank_model: str = 'gte-rerank-v2'
    milvus_uri: str = 'http://localhost:19530'
    milvus_token: str = ''
    milvus_collection: str = 'coach_evidence_v1'
    model_timeout: float = 60
    max_agent_steps: int = Field(10, ge=3, le=30)
    # 单次评估的候选上限：每位候选都要经过评估 Agent，不能无上限。超出时会如实告知用户。
    max_candidates: int = Field(30, ge=5, le=200)
    log_level: str = 'INFO'
    log_format: Literal['json', 'text'] = 'json'
    retrieval_top_k: int = 15
    rrf_k: int = 60
    rerank_top_n: int = 8
    max_parents: int = 6
    max_context_chars: int = 9000

    def require_live_keys(self):
        missing = [name for name in ('chat_api_key', 'dashscope_api_key') if not getattr(self, name)]
        if missing:
            raise RuntimeError('真实模式缺少配置：' + ', '.join(missing))


@lru_cache
def get_settings():
    return Settings()
