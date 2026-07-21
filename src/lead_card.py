"""AKO 网站咨询网关 - 留资卡片 + 留资保存"""

import json
import os
from datetime import datetime, timezone, timedelta

from src.config import settings
from src.models import ChatActionResponse, LeadRequest

_LOCAL_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai


def build_lead_payload(session_id: str) -> ChatActionResponse:
    """构建留资引导话术"""
    return ChatActionResponse(
        session_id=session_id,
        action="lead",
        answer="这个问题我为您转接专属顾问，请留下联系方式，稍后第一时间回复您。",
        sources=[],
        score=0.0,
    )


def save_lead(lead: LeadRequest) -> None:
    """追加写 data/leads.jsonl（一行一 JSON，含时间戳）"""
    leads_path = os.path.join(settings.data_dir, "leads.jsonl")
    record = {
        "ts": datetime.now(_LOCAL_TZ).isoformat(timespec="seconds"),
        "name": lead.name,
        "phone": lead.phone,
        "market": lead.market,
        "message": lead.message,
    }
    os.makedirs(settings.data_dir, exist_ok=True)
    with open(leads_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")