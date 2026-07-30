# ============================================
# Author: AKO_studio
# Agent: AKO_web_consult_agent
# Generated: 2026-07-30
# ============================================
#
"""AKO 网站咨询网关 - 会话管理（内存字典）"""

import time
from typing import Optional
from src.config import settings


class SessionManager:
    """内存会话管理器，惰性过期清理"""

    def __init__(self):
        self._sessions: dict[str, dict] = {}

    def get_or_create(self, session_id: Optional[str] = None) -> tuple[str, list]:
        """获取或创建会话，返回 (session_id, history)"""
        self._cleanup_expired()

        if session_id and session_id in self._sessions:
            s = self._sessions[session_id]
            s["last_active"] = time.time()
            return session_id, s["history"]

        new_id = session_id or f"ako-{int(time.time() * 1000)}"
        self._sessions[new_id] = {
            "history": [],
            "last_active": time.time(),
        }
        return new_id, []

    def append(self, session_id: str, question: str, answer: str) -> None:
        """追加一轮对话，超出 max_history_rounds 截断最早轮次"""
        if session_id not in self._sessions:
            return
        s = self._sessions[session_id]
        s["history"].append((question, answer))
        s["last_active"] = time.time()
        # 截断：只保留最近 max_history_rounds 轮
        max_rounds = settings.session_max_rounds
        if len(s["history"]) > max_rounds:
            s["history"] = s["history"][-max_rounds:]

    def _cleanup_expired(self) -> None:
        """惰性清理过期会话（TTL > session_ttl_min 分钟）"""
        now = time.time()
        ttl_seconds = settings.session_ttl_min * 60
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s["last_active"] > ttl_seconds
        ]
        for sid in expired:
            del self._sessions[sid]


# 全局单例
session_manager = SessionManager()