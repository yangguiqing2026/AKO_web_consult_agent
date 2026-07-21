"""AKO 网站咨询网关 - FastAPI 装配（:7863，单进程 <500MB）"""

import asyncio
import json
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import settings
from src.models import ChatRequest, ChatActionResponse, LeadRequest, SourceItem
from src.retriever import retriever
from src.intent_router import route as intent_route
from src.llm_router import chat_stream, AllLLMFailed
from src.session import session_manager
from src.source_mask import mask, load_source_map
from src.lead_card import build_lead_payload, save_lead
from src.logger import log_qa
from src.wall_query import wall_query
from src.wall_answer import answer_panels, answer_specs, answer_pricing, extract_thickness

import re

_LOCAL_TZ = timezone(timedelta(hours=8))

# 流式输出清洗：去除 markdown 格式 + 引用标记 + 换行
_MD_RE = re.compile(r'(\*\*|\*|##|###|\n)')
_CITE_RE = re.compile(r'\[\d+\]')

# ==== 内存限流 ====
_rate_counter: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str) -> bool:
    """返回 True 表示未超限"""
    now = time.time()
    window = 60.0  # 1 分钟
    limit = settings.rate_limit_per_min

    # 清理过期记录
    _rate_counter[client_ip] = [
        t for t in _rate_counter[client_ip] if now - t < window
    ]

    if len(_rate_counter[client_ip]) >= limit:
        return False

    _rate_counter[client_ip].append(now)
    return True


# ==== 敏感词检查 ====
_sensitive_words: list[str] = []


def _load_sensitive_words() -> None:
    """加载敏感词列表（支持 # 注释行）"""
    global _sensitive_words
    path = os.path.join(settings.data_dir, "sensitive_words.txt")
    words = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line.lower())
    except FileNotFoundError:
        pass
    _sensitive_words = words


def _check_sensitive(text: str) -> bool:
    """返回 True 表示命中敏感词"""
    t = text.lower()
    return any(w and w in t for w in _sensitive_words)


# ==== 限流中间件 ====
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 不对 health/admin 路径限流
        if request.url.path in ("/health", "/api/admin/threshold"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip):
            return JSONResponse(
                status_code=429,
                content={
                    "session_id": "",
                    "action": "refuse",
                    "answer": "您的咨询次数过于频繁，请稍后再试。如急需帮助，可拨打客服热线或通过微信公众号联系我们的专属顾问。",
                    "sources": [],
                    "score": 0.0,
                },
            )
        return await call_next(request)


# ==== 生命周期 ====
async def _periodic_refresh():
    """每 10 分钟刷新 BM25"""
    while True:
        await asyncio.sleep(600)
        try:
            retriever.refresh()
            print("[main] BM25 索引已刷新")
        except Exception as e:
            print(f"[main] BM25 刷新失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭"""
    print("[main] AKO 网站咨询网关启动中...")

    # 初始化检索器（版本校验失败会抛异常退出）
    try:
        retriever.initialize()
    except RuntimeError as e:
        print(f"[main] 检索器初始化失败，进程退出: {e}")
        raise SystemExit(1) from e

    # 加载来源映射
    load_source_map()

    # 加载敏感词
    _load_sensitive_words()

    # 启动后台刷新任务
    task = asyncio.create_task(_periodic_refresh())

    print(f"[main] 启动完成，监听端口 :{settings.port}")
    yield

    # 关闭时取消后台任务
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("[main] AKO 网站咨询网关已关闭")


# ==== FastAPI 应用 ====
app = FastAPI(title="AKO 网站咨询网关", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://akobuild.cloud", "https://12563zyom2117.vicp.fun"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)


# ==== 端点 ====

@app.get("/health")
async def health():
    """健康检查端点 — DB-002 增强：含 wall_api 状态"""
    wall_status = "disabled"
    if settings.wall_api_enabled:
        try:
            rows = await wall_query("meta")
            wall_status = "up" if rows else "down"
        except Exception:
            wall_status = "down"

    return {
        "ok": True,
        "collection": settings.allowed_collections[0],
        "doc_count": retriever.doc_count,
        "kb_updated_at": retriever.kb_updated_at,
        "embedding_model": retriever.embedding_model,
        "wall_api": wall_status,
    }


@app.get("/")
async def index():
    """返回前端咨询组件"""
    html_path = os.path.join(
        os.path.dirname(__file__), "static", "consult_widget.html"
    )
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>AKO 咨询组件未找到</h1>", status_code=404
        )


@app.post("/api/chat")
async def api_chat(req: ChatRequest, request: Request):
    """核心问答接口（SSE 流式 or 非流式动作）"""
    # 获取客户端信息
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    # 会话恢复
    session_id, history = session_manager.get_or_create(req.session_id)
    question = req.question.strip()

    # 敏感词检查
    if _check_sensitive(question):
        resp = ChatActionResponse(
            session_id=session_id,
            action="refuse",
            answer="抱歉，您的问题中包含敏感信息，我们无法为您解答。如有其他问题，欢迎继续咨询。",
        )
        log_qa({
            "session_id": session_id,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "question": question,
            "action": "refuse",
            "score": 0.0,
            "sources": [],
            "aborted": False,
        })
        return JSONResponse(content=resp.model_dump())

    # 意图路由
    action = intent_route(question)

    # === WallDB 结构化直答分支（DB-002 §1.1） ===
    text: str = ""
    if action == "struct_panels":
        rows = await wall_query("panels")
        if rows:
            text = answer_panels(rows)
        else:
            print("[main] WARN wall_api_down: struct_panels 回退 FAQ")
            action = "faq"

    if action == "struct_specs":
        thk = extract_thickness(question)
        rows = await wall_query("specs", thk)
        if rows:
            text = answer_specs(rows, question)
        else:
            print("[main] WARN wall_api_down: struct_specs 回退 FAQ")
            action = "faq"

    if action == "struct_pricing":
        rows = await wall_query("pricing")
        if rows:
            text = answer_pricing(rows)
        else:
            print("[main] WARN wall_api_down: struct_pricing 回退留资")
            action = "lead"

    # 结构化直答成功 → 非流式返回（action=answer，source=《阿格墙板数据库》）
    if action in ("struct_panels", "struct_specs", "struct_pricing"):
        source_label = "《阿格墙板数据库》"
        resp = ChatActionResponse(
            session_id=session_id,
            action="answer",
            answer=text,
            sources=[SourceItem(display_name=source_label, score=1.0)],
            score=1.0,
        )
        log_qa({
            "session_id": session_id,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "question": question,
            "action": "answer",
            "score": 1.0,
            "sources": [source_label],
            "aborted": False,
        })
        # 结构化直答也保存历史
        session_manager.append(session_id, question, text)
        return JSONResponse(content=resp.model_dump())

    # 非 FAQ 动作直接返回
    if action == "lead":
        resp = build_lead_payload(session_id)
        log_qa({
            "session_id": session_id,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "question": question,
            "action": "lead",
            "score": 0.0,
            "sources": [],
            "aborted": False,
        })
        return JSONResponse(content=resp.model_dump())

    if action == "chitchat":
        resp = ChatActionResponse(
            session_id=session_id,
            action="chitchat",
            answer="我是阿格建筑咨询助手，可为您解答陶粒墙板产品与应用的疑问。",
        )
        log_qa({
            "session_id": session_id,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "question": question,
            "action": "chitchat",
            "score": 0.0,
            "sources": [],
            "aborted": False,
        })
        return JSONResponse(content=resp.model_dump())

    # === FAQ 路径：检索 + LLM 生成 ===
    docs = await retriever.query(question)
    max_score = docs[0].score if docs else 0.0

    # 低分兜底 → 留资
    if max_score < settings.score_threshold or not docs:
        resp = build_lead_payload(session_id)
        resp.score = max_score
        log_qa({
            "session_id": session_id,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "question": question,
            "action": "lead",
            "score": max_score,
            "sources": [],
            "aborted": False,
        })
        return JSONResponse(content=resp.model_dump())

    # 脱敏 sources
    masked_sources = [
        SourceItem(display_name=mask(d.source), score=d.score)
        for d in docs
    ]

    # SSE 流式响应
    async def generate_sse():
        full_answer: str = ""
        aborted: bool = False
        final_action: str = "answer"

        try:
            # 发送 meta 事件
            meta_data = {
                "session_id": session_id,
                "action": "answer",
                "sources": [s.model_dump() for s in masked_sources],
                "score": max_score,
            }
            yield f"event: meta\ndata: {json.dumps(meta_data, ensure_ascii=False)}\n\n"

            # 流式生成
            async for token in chat_stream(question, docs, history):
                clean_token = _CITE_RE.sub("", _MD_RE.sub("", token))
                if clean_token.strip():
                    full_answer += clean_token
                    yield f"event: delta\ndata: {json.dumps({'content': clean_token}, ensure_ascii=False)}\n\n"

            # 发送 done
            done_data = {"score": max_score}
            yield f"event: done\ndata: {json.dumps(done_data)}\n\n"

        except AllLLMFailed:
            # 全部 LLM 失败，降级
            final_action = "degraded"
            fallback = "正在为您查询，稍后会有专属顾问联系您。如您有紧急需求，可拨打客服热线联系我们的服务团队。"
            yield f"event: meta\ndata: {json.dumps({'session_id': session_id, 'action': 'degraded', 'sources': [s.model_dump() for s in masked_sources], 'score': max_score}, ensure_ascii=False)}\n\n"
            yield f"event: delta\ndata: {json.dumps({'content': fallback}, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'score': max_score})}\n\n"
            full_answer = fallback

        except asyncio.CancelledError:
            aborted = True
            final_action = "answer"

        finally:
            # 保存历史
            if full_answer and not aborted:
                session_manager.append(session_id, question, full_answer)

            # 记日志（脱敏后）
            log_qa({
                "session_id": session_id,
                "client_ip": client_ip,
                "user_agent": user_agent,
                "question": question,
                "action": final_action,
                "score": max_score,
                "sources": [s.display_name for s in masked_sources],
                "aborted": aborted,
            })

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/lead")
async def api_lead(lead: LeadRequest):
    """留资提交"""
    save_lead(lead)
    return {"ok": True}


@app.get("/api/admin/threshold")
async def get_threshold(token: str):
    """读当前阈值"""
    if token != settings.admin_token:
        return JSONResponse(status_code=403, content={"error": "invalid token"})
    return {"score_threshold": settings.score_threshold}


@app.post("/api/admin/threshold")
async def set_threshold(token: str, request: Request):
    """热调阈值（免重启）"""
    if token != settings.admin_token:
        return JSONResponse(status_code=403, content={"error": "invalid token"})
    try:
        body = await request.json()
        new_val = float(body.get("score_threshold", settings.score_threshold))
        if 0.0 <= new_val <= 1.0:
            settings.score_threshold = new_val
            return {"score_threshold": new_val, "ok": True}
        else:
            return JSONResponse(
                status_code=400, content={"error": "threshold must be 0~1"}
            )
    except (ValueError, json.JSONDecodeError):
        return JSONResponse(status_code=400, content={"error": "invalid value"})