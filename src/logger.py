"""AKO 网站咨询网关 - 问答日志（JSONL 按日分割）"""

import json
import os
from datetime import datetime, timezone, timedelta

from src.config import settings

_LOCAL_TZ = timezone(timedelta(hours=8))


def log_qa(record: dict) -> None:
    """追加写 logs/qa-YYYYMMDD.jsonl。

    必含字段：ts, session_id, client_ip, user_agent, question, action, score, sources, aborted。
    不留资字段（name/phone 只进 leads.jsonl）。
    """
    today = datetime.now(_LOCAL_TZ).strftime("%Y%m%d")
    log_path = os.path.join(settings.log_dir, f"qa-{today}.jsonl")

    full_record = {
        "ts": record.get("ts", datetime.now(_LOCAL_TZ).isoformat(timespec="seconds")),
        "session_id": record.get("session_id", ""),
        "client_ip": record.get("client_ip", ""),
        "user_agent": record.get("user_agent", ""),
        "question": record.get("question", ""),
        "action": record.get("action", ""),
        "score": record.get("score", 0.0),
        "sources": record.get("sources", []),   # 已脱敏后的显示名列表
        "aborted": record.get("aborted", False),
    }

    os.makedirs(settings.log_dir, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(full_record, ensure_ascii=False) + "\n")