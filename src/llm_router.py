"""AKO 网站咨询网关 - LLM 三级级联流式生成"""

import json
from typing import AsyncIterator, List

import httpx

from src.config import settings
from src.models import RetrieveItem

_SYSTEM_PROMPT = """你是阿格建筑（AKO）客户，正在帮朋友回答产品问题。请依据下方参考资料，用口语直接回答：
- 第一句就说答案，绝不加"根据""依据""参考"等前缀
- 一句话说完，不要换行、不要列表、不要编号
- 纯口语，不用任何格式符号
- 不知道就说"建议联系专属顾问"
"""


class AllLLMFailed(Exception):
    """所有三级 LLM 均失败"""
    pass


def _approx_token_count(text: str) -> int:
    """按 1 token ≈ 1.5 中文字符估算 token 数"""
    return max(1, len(text) * 2 // 3)


def build_messages(
    question: str, docs: List[RetrieveItem], history: List[tuple]
) -> list[dict]:
    """上下文装配：文档总量 ≤ max_context_tokens，历史 ≤ max_history_rounds"""
    # 只带最近 max_history_rounds 轮历史
    max_rounds = settings.max_history_rounds
    recent_history = history[-max_rounds:] if len(history) > max_rounds else history

    # 构建参考资料文本（控制 token 预算）
    budget = settings.max_context_tokens
    doc_texts = []
    used_tokens = 0
    for i, doc in enumerate(docs):
        block = doc.text
        block_tokens = _approx_token_count(block)
        if used_tokens + block_tokens > budget:
            # 超出预算，后面文档截断（仍有剩余预算时尽量放缩略版）
            if used_tokens < budget:
                remaining = budget - used_tokens - _approx_token_count("（内容过长已截断）")
                if remaining > 50:
                    truncated = doc.text[:(remaining * 3 // 2)]
                    doc_texts.append(f"{truncated}...（已截断）")
            break
        doc_texts.append(block)
        used_tokens += block_tokens

    context_text = "\n\n".join(doc_texts) if doc_texts else "暂无参考资料"

    # 组装 messages
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

    # 历史对话
    for q, a in recent_history:
        messages.append({"role": "user", "content": q})
        messages.append({"role": "assistant", "content": a})

    # 当前问题
    messages.append({
        "role": "user",
        "content": f"【参考资料】\n{context_text}\n\n【用户问题】\n{question}"
    })

    return messages


async def _stream_minimax(
    messages: list[dict],
) -> AsyncIterator[str]:
    """P0: MiniMax OpenAI 兼容流式接口"""
    if not settings.minimax_api_key:
        raise Exception("MiniMax API key 未配置")

    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    headers = {
        "Authorization": f"Bearer {settings.minimax_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.minimax_model,
        "messages": messages,
        "stream": True,
        "temperature": 0.3,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line and line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue


async def _stream_kimi(
    messages: list[dict],
) -> AsyncIterator[str]:
    """P1: Kimi OpenAI 兼容流式接口"""
    if not settings.kimi_api_key:
        raise Exception("Kimi API key 未配置")

    url = "https://api.moonshot.cn/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.kimi_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.kimi_model,
        "messages": messages,
        "stream": True,
        "temperature": 0.3,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line and line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue


async def _stream_ollama(
    messages: list[dict],
) -> AsyncIterator[str]:
    """P2: Ollama /api/chat 流式"""
    url = f"{settings.ollama_base}/api/chat"
    body = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": 0.3,
        },
    }

    async with httpx.AsyncClient(timeout=float(settings.ollama_timeout_s)) as client:
        async with client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError):
                        continue


async def chat_stream(
    question: str, docs: List[RetrieveItem], history: List[tuple]
) -> AsyncIterator[str]:
    """三级级联流式生成：MiniMax → Kimi → Ollama，全部失败抛 AllLLMFailed"""
    messages = build_messages(question, docs, history)

    # P0: MiniMax
    try:
        async for token in _stream_minimax(messages):
            yield token
        return
    except Exception as e:
        print(f"[llm_router] MiniMax 失败，降级到 Kimi: {e}")

    # P1: Kimi
    try:
        async for token in _stream_kimi(messages):
            yield token
        return
    except Exception as e:
        print(f"[llm_router] Kimi 失败，降级到 Ollama: {e}")

    # P2: Ollama
    try:
        async for token in _stream_ollama(messages):
            yield token
        return
    except Exception as e:
        print(f"[llm_router] Ollama 失败，全部降级: {e}")

    raise AllLLMFailed("所有 LLM 均不可用")