# ============================================
# Author: AKO_studio
# Agent: AKO_web_consult_agent
# Generated: 2026-07-30
# ============================================
#
"""AKO 网站咨询网关数据模型 - Pydantic v2"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class SourceItem(BaseModel):
    display_name: str
    score: float


class ChatActionResponse(BaseModel):
    """非流式动作（留资/拒答/降级）"""

    session_id: str
    action: Literal["answer", "lead", "refuse", "chitchat", "degraded"]
    answer: str = ""
    sources: list[SourceItem] = []
    score: float = 0.0


class LeadRequest(BaseModel):
    name: str
    phone: str
    market: Literal["城市更新", "文旅民宿", "乡村民居"]
    message: str = ""


class RetrieveItem(BaseModel):
    """与 KnowledgeHub.query() 同名字段"""

    text: str
    source: str
    score: float
    dense_score: float
    sparse_score: float