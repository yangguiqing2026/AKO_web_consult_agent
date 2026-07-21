"""AKO 网站咨询网关配置 - pydantic-settings BaseSettings，支持 .env 覆盖"""

from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 7863
    chroma_root: str = r"D:\AKO_knowledge"
    allowed_collections: List[str] = ["ako_taoli_general_arch"]
    embed_model: str = "bge-m3"  # 必须与发布集 metadata.embedding_model_version 一致
    ollama_base: str = "http://localhost:11434"
    top_k: int = 5
    candidate_k: int = 20  # 每路召回数
    rrf_k: int = 60
    rrf_w_dense: float = 0.7
    rrf_w_sparse: float = 0.3
    score_threshold: float = 0.6  # 支持 /api/admin/threshold 热调
    max_context_tokens: int = 2000  # 检索文档总量预算
    max_history_rounds: int = 3  # 带入生成的历史轮数
    session_ttl_min: int = 30
    session_max_rounds: int = 10
    rate_limit_per_min: int = 10
    minimax_api_key: str = ""
    minimax_model: str = "abab6.5s-chat"
    kimi_api_key: str = ""
    kimi_model: str = "moonshot-v1-auto"
    ollama_model: str = "qwen2.5"
    ollama_timeout_s: int = 10
    admin_token: str = "change-me"
    data_dir: str = r"D:\AKO_web_consult\data"
    log_dir: str = r"D:\AKO_web_consult\logs"

    # Phase 1 imports (Phase 2 会用到，先预留)
    hub_meta_db: str = r"D:\AKO_Hub\hub_meta.db"
    geo_output_dir: str = r"D:\AKO_Hub\geo_output"

    # Wall API (DB-002)
    wall_api_base: str = "http://wh-nc6lcdplh894m2oe8v0.my3w.com/wall_api.php"
    wall_api_timeout: float = 10.0
    wall_api_cache_ttl: int = 60
    wall_api_enabled: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()