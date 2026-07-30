# ============================================
# Author: AKO_studio
# Agent: AKO_web_consult_agent
# Generated: 2026-07-30
# ============================================
#
"""AKO 网站咨询网关 - 发布集双路检索 + 加权 RRF 融合"""

import asyncio
import time
from typing import List, Optional

import chromadb
import httpx
import jieba
from chromadb import PersistentClient
from rank_bm25 import BM25Okapi

from src.config import settings
from src.models import RetrieveItem


class PublishRetriever:
    """发布集检索器：Dense（Ollama embedding） + Sparse（BM25/jieba）→ RRF 融合"""

    def __init__(self):
        self._client: Optional[PersistentClient] = None
        self._collection = None
        self._bm25: Optional[BM25Okapi] = None
        self._doc_texts: List[str] = []
        self._doc_sources: List[str] = []
        self._kb_updated_at: str = ""
        self._embedding_model: str = ""

    def initialize(self) -> None:
        """启动时连接 ChromaDB，校验版本，建 BM25"""
        chroma_root = settings.chroma_root
        allowed = settings.allowed_collections
        expected_model = settings.embed_model

        try:
            self._client = chromadb.PersistentClient(path=chroma_root)
            coll_name = allowed[0]

            try:
                self._collection = self._client.get_collection(coll_name)
            except Exception:
                raise RuntimeError(
                    f"Collection '{coll_name}' 不存在于 {chroma_root}，"
                    f"请确认发布集 {coll_name} 已灌入。"
                )

            # 校验 embedding model 版本
            metadata = self._collection.metadata or {}
            actual_model = metadata.get("embedding_model_version", "")
            self._embedding_model = actual_model

            if actual_model and actual_model != expected_model:
                raise RuntimeError(
                    f"Embedding 模型不一致：发布集使用 '{actual_model}'，"
                    f"配置为 '{expected_model}'。请修改 .env 中的 embed_model"
                    f" 为 '{actual_model}' 后重启。"
                )

            # 记录知识库更新时间
            self._kb_updated_at = metadata.get(
                "updated_at",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            )

            # 全量拉取文档建 BM25
            self._rebuild_bm25()

            print(f"[retriever] 初始化完成: collection={coll_name}, "
                  f"docs={len(self._doc_texts)}, model={actual_model or expected_model}")

        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"PublishRetriever 初始化失败: {e}")

    def _rebuild_bm25(self) -> None:
        """全量拉取发布集文档，重建 BM25 索引"""
        if self._collection is None:
            return

        results = self._collection.get()
        self._doc_texts = results.get("documents", []) or []
        metas = results.get("metainfos", []) or []
        self._doc_sources = [
            m.get("source", "") if isinstance(m, dict) else ""
            for m in metas
        ]

        if self._doc_texts:
            tokenized = [list(jieba.cut(t)) for t in self._doc_texts]
            self._bm25 = BM25Okapi(tokenized)
        else:
            self._bm25 = None

    def refresh(self) -> None:
        """重建 BM25（供定时器每 10 min 调用）"""
        self._rebuild_bm25()

    @property
    def doc_count(self) -> int:
        return len(self._doc_texts)

    @property
    def kb_updated_at(self) -> str:
        return self._kb_updated_at

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    async def query(self, question: str) -> List[RetrieveItem]:
        """双路检索 + RRF 融合，返回 top_k 结果（score 归一化到 0~1）"""
        loop = asyncio.get_event_loop()

        # Dense 检索
        dense_results = await self._dense_retrieve(question)

        # Sparse 检索（BM25）
        sparse_results = self._sparse_retrieve(question)

        if not dense_results and not sparse_results:
            return []

        # RRF 融合
        rrf_k = settings.rrf_k
        w_dense = settings.rrf_w_dense
        w_sparse = settings.rrf_w_sparse

        # 计算每篇文档的 RRF 分数
        scores: dict[int, float] = {}
        for rank, idx in enumerate(dense_results):
            scores[idx] = scores.get(idx, 0.0) + w_dense / (rrf_k + rank + 1)
        for rank, idx in enumerate(sparse_results):
            scores[idx] = scores.get(idx, 0.0) + w_sparse / (rrf_k + rank + 1)

        # 理论最大值（rank=1 时）
        max_possible = w_dense / (rrf_k + 1) + w_sparse / (rrf_k + 1)

        # 取 top_k，归一化
        top_k = settings.top_k
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        items = []
        for idx, fused_score in sorted_items:
            # 归一化到 0~1
            normalized = min(fused_score / max_possible if max_possible > 0 else 0.0, 1.0)

            # 取单路分数（如果某路没召回到，给 0）
            dense_score = 0.0
            if idx < len(self._doc_texts):
                # 从结果中找对应分数
                dense_score = 0.0  # RRF 不保留原始分，用 rank 反推估算
                if idx in dense_results:
                    rank_pos = dense_results.index(idx)
                    dense_score = 1.0 / (rrf_k + rank_pos + 1) / (1.0 / (rrf_k + 1))

            sparse_score = 0.0
            if idx in sparse_results:
                rank_pos = sparse_results.index(idx)
                sparse_score = 1.0 / (rrf_k + rank_pos + 1) / (1.0 / (rrf_k + 1))

            items.append(RetrieveItem(
                text=self._doc_texts[idx] if idx < len(self._doc_texts) else "",
                source=self._doc_sources[idx] if idx < len(self._doc_sources) else "",
                score=round(normalized, 4),
                dense_score=round(dense_score, 4),
                sparse_score=round(sparse_score, 4),
            ))

        return items

    async def _dense_retrieve(self, question: str) -> List[int]:
        """Dense 检索：Ollama embedding → ChromaDB query，返回文档下标列表"""
        if self._collection is None:
            return []

        try:
            # 获取 query embedding
            embed_url = f"{settings.ollama_base}/api/embeddings"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    embed_url,
                    json={"model": settings.embed_model, "prompt": question},
                )
                resp.raise_for_status()
                data = resp.json()
                embedding = data.get("embedding", [])
                if not embedding:
                    return []

            # 查询 ChromaDB
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(settings.candidate_k, len(self._doc_texts)),
            )

            # 返回文档下标
            ids = results.get("ids", [[]])[0]
            all_ids = self._collection.get().get("ids", [])
            indices = []
            for doc_id in ids:
                try:
                    indices.append(all_ids.index(doc_id))
                except ValueError:
                    pass
            return indices

        except Exception as e:
            print(f"[retriever] Dense 检索异常: {e}")
            return []

    def _sparse_retrieve(self, question: str) -> List[int]:
        """Sparse 检索：BM25 + jieba 分词，返回文档下标列表"""
        if self._bm25 is None or not self._doc_texts:
            return []

        tokenized_query = list(jieba.cut(question))
        scores = self._bm25.get_scores(tokenized_query)

        # 取 top candidate_k
        candidate_k = min(settings.candidate_k, len(scores))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return ranked[:candidate_k]


# 全局单例
retriever = PublishRetriever()