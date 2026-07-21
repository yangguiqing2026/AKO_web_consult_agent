# 任务：AKO_web_consult Phase 1 —— 网站咨询网关骨架

> 用法：本文件整篇贴给 VS Code Copilot Chat（或逐 `=== FILE ===` 段粘贴）。
> 依据文档：AGE-TECH-AKO-WEB-001《AKO 网站咨询系统架构白皮书》v1.0.0。
> 本 Phase 只做骨架：跑通「提问 → 白名单检索 → LLM 生成 → 引用脱敏 → SSE 流式返回」全链路，低置信转留资兜底。

## 0. 结论前置

在 `D:\AKO_web_consult` 新建 FastAPI 咨询网关（单进程，:7863，内存 <500MB），检索端只查 ChromaDB 发布集 `ako_taoli_web_arch`（chroma_root = `D:\AKO_knowledge`），生成端走 MiniMax → Kimi → Ollama 三级路由 + 静态兜底。验收 = §3.3 冒烟 5 项全过。**不写 Phase 2 内容**（完整意图规则表、hub_meta.db 回流、前端品牌化均不做）。

## 1. 实现清单（逐项打勾）

### 1.1 目录结构（按路径直接创建）

```
D:\AKO_web_consult\
├── src\
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── intent_router.py
│   ├── retriever.py
│   ├── llm_router.py
│   ├── session.py
│   ├── source_mask.py
│   ├── lead_card.py
│   ├── logger.py
│   └── static\consult_widget.html
├── data\
│   ├── source_map.json
│   └── sensitive_words.txt
├── logs\
├── tests\smoke_test.py
├── requirements.txt
└── start.bat
```

=== FILE: requirements.txt ===

```
fastapi
uvicorn[standard]
pydantic>=2
pydantic-settings
chromadb==<与 D:\AKO_Hub 当前环境同版本，先查后钉>
httpx
rank_bm25
jieba
```

=== FILE: src/config.py ===

pydantic-settings `BaseSettings`，支持 `.env` 覆盖。字段与默认值：

```python
class Settings(BaseSettings):
    port: int = 7863
    chroma_root: str = r"D:\AKO_knowledge"
    allowed_collections: list[str] = ["ako_taoli_web_arch"]
    embed_model: str = "bge-m3"          # 必须与发布集 metadata.embedding_model_version 一致
    ollama_base: str = "http://localhost:11434"
    top_k: int = 5
    candidate_k: int = 20                # 每路召回数
    rrf_k: int = 60
    rrf_w_dense: float = 0.7
    rrf_w_sparse: float = 0.3
    score_threshold: float = 0.6         # 支持 /api/admin/threshold 热调
    max_context_tokens: int = 2000       # 检索文档总量预算
    max_history_rounds: int = 3          # 带入生成的历史轮数
    session_ttl_min: int = 30
    session_max_rounds: int = 10
    rate_limit_per_min: int = 10
    minimax_api_key: str = ""
    minimax_model: str = "abab6.5s-chat"
    kimi_api_key: str = ""
    kimi_model: str = "kimi-latest"
    ollama_model: str = "qwen2.5"
    ollama_timeout_s: int = 10
    admin_token: str = "change-me"
    data_dir: str = r"D:\AKO_web_consult\data"
    log_dir: str = r"D:\AKO_web_consult\logs"
```

=== FILE: src/models.py ===

Pydantic v2：

```python
class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None

class SourceItem(BaseModel):
    display_name: str
    score: float

class ChatActionResponse(BaseModel):     # 非流式动作（留资/拒答/降级）
    session_id: str
    action: Literal["answer", "lead", "refuse", "degraded"]
    answer: str = ""
    sources: list[SourceItem] = []
    score: float = 0.0

class LeadRequest(BaseModel):
    name: str
    phone: str
    market: Literal["城市更新", "文旅民宿", "乡村民居"]
    message: str = ""

class RetrieveItem(BaseModel):           # 与 KnowledgeHub.query() 同名字段
    text: str
    source: str
    score: float
    dense_score: float
    sparse_score: float
```

=== FILE: src/retriever.py ===

职责：发布集双路检索 + 加权 RRF 融合。要点：

- 启动时：连接 `PersistentClient(chroma_root)` → 校验 collection 在白名单 → 读取 `collection.metadata["embedding_model_version"]`，与 `settings.embed_model` 不一致则**抛错拒绝启动** → 全量拉取发布集文档（≤100 篇量级）建内存 BM25（jieba 分词）
- `refresh()`：重拉文档重建 BM25，供定时器每 10 min 调用
- `query(question) -> list[RetrieveItem]`：
  - Dense：调 Ollama `/api/embeddings`（模型 = `settings.embed_model`）得 query 向量 → `collection.query(query_embeddings=..., n_results=candidate_k)`
  - Sparse：BM25 top-`candidate_k`
  - 融合：`score = w_dense/(rrf_k + rank_dense) + w_sparse/(rrf_k + rank_sparse)`，按 score 降序取 top_k
  - 归一化：将融合分映射到 0~1（除以理论最大值 `w_dense/(rrf_k+1) + w_sparse/(rrf_k+1)`），使阈值 0.6 有可比语义

=== FILE: src/intent_router.py ===

Phase 1 关键词规则，函数签名：

```python
def route(question: str) -> Literal["lead", "chitchat", "faq"]
```

- `lead`：命中 {价格, 报价, 多少钱, 合作, 代理, 加盟, 参观, 样板房, 电话, 微信, 联系}
- `chitchat`：命中 {你好, 在吗, 谢谢, 再见} 或与建筑/墙板无关（长度 <4 且无名词命中）
- 其余 → `faq`

=== FILE: src/session.py ===

内存字典 `{session_id: {"history": [(q, a)], "last_active": ts}}`：

- `get_or_create(session_id | None) -> (session_id, history)`
- `append(session_id, q, a)`：超 `session_max_rounds` 截断最早轮次
- 惰性过期清理（TTL 30 min）

=== FILE: src/source_mask.py ===

- 启动加载 `data/source_map.json`
- `mask(internal_source: str) -> str`：命中返回对外显示名；未命中返回 `《阿格产品资料》`

=== FILE: src/llm_router.py ===

职责：上下文装配 + 三级级联流式生成。

```python
def build_messages(question, docs: list[RetrieveItem], history) -> list[dict]
async def chat_stream(question, docs, history) -> AsyncIterator[str]   # 逐级降级，全部失败抛 AllLLMFailed
```

- 系统提示词约束：只依据给定资料回答；工程术语严谨；引用标注 `[1]` `[2]`；不知道就明说，禁止编造；temperature=0.3
- **上下文预算**：`build_messages` 内按字符近似（1 token ≈ 1.5 中文字符）截断——文档总量 ≤ `max_context_tokens`（超出截断靠后文档），历史只带最近 `max_history_rounds` 轮
- P0 MiniMax：httpx 异步流式（OpenAI 兼容接口）；异常/超时 → P1 Kimi 同构调用；再失败 → P2 Ollama `/api/chat`（流式，`timeout=ollama_timeout_s`）；再失败 → 抛 `AllLLMFailed`
- 单级中途断流视为该级失败，降级到下一级重新生成

=== FILE: src/lead_card.py ===

- `build_lead_payload(session_id) -> ChatActionResponse`：话术 = "这个问题我为您转接专属顾问，请留下联系方式，稍后第一时间回复您。" + `action="lead"`
- `save_lead(lead: LeadRequest) -> None`：追加写 `data/leads.jsonl`（一行一 JSON，含时间戳）

=== FILE: src/logger.py ===

- `log_qa(record: dict) -> None`：追加写 `logs/qa-YYYYMMDD.jsonl`
- 记录字段：`ts, session_id, client_ip, user_agent, question, action, score, sources(脱敏后), aborted`
- **不记留资字段**（name/phone 只进 leads.jsonl）

=== FILE: src/main.py ===

FastAPI 装配：

- 启动：`PublishRetriever` 初始化（版本校验失败 → 进程退出并打印原因）；BM25 每 10 min 后台刷新
- 内存限流中间件：单 IP `rate_limit_per_min` 次/min，超限返回 429 固定话术
- 敏感词检查：命中 `data/sensitive_words.txt` → `action="refuse"` 固定话术
- `POST /api/chat`：
  1. 会话恢复 → 意图路由
  2. `lead` → 返回留资 payload；`chitchat` → 固定话术（"我是阿格建筑咨询助手，可为您解答陶粒墙板产品与应用的疑问"）
  3. `faq` → 检索 → 最高分 < 阈值 → 留资兜底 payload
  4. 否则 `StreamingResponse` SSE：`meta`（session_id、action、脱敏 sources）→ `delta`（逐段）→ `done`（score）
  5. `AllLLMFailed` → SSE 发 `action="degraded"` 降级话术（"正在为您查询，稍后会有专属顾问联系您"）+ 留资引导
  6. 捕获客户端断连（`asyncio.CancelledError` / Starlette disconnect）→ 记 `aborted=True`，不抛错
- `POST /api/lead`：校验 → 落盘 → `{"ok": true}`
- `GET /api/admin/threshold?token=` / `POST /api/admin/threshold`：读/热调 `score_threshold`（改 settings 运行时值，免重启）
- `GET /health`：`{"ok", "collection", "doc_count", "kb_updated_at", "embedding_model"}`（`kb_updated_at` 取发布集 metadata 或最新文档入库时间）
- `GET /`：返回 `static/consult_widget.html`

=== FILE: src/static/consult_widget.html ===

Phase 1 极简版：右下角悬浮气泡 → 展开对话窗；fetch 流式读取 SSE（`response.body.getReader()`），marked.js 渲染；`action=lead` 时窗内渲染留资表单（姓名/电话/意向市场下拉：城市更新/文旅民宿/乡村民居）提交 `/api/lead`；底部固定「知识库更新至 {kb_updated_at}」（取 `/health`）。AKO 色系：底 `#EBDAB9`、过渡 `#C3BEB4`、锚点 `#231E1C`、点缀 `#A08C64`、标题 `#B99B5F`；禁用纯白 `#FFFFFF` 背景。Tailwind CDN + marked.js CDN，单文件零构建。

=== FILE: tests/smoke_test.py ===

`requests` 顺序执行 5 项（见 §3.3），逐项打印 PASS/FAIL，任一 FAIL 退出码非零。

=== FILE: start.bat ===

```bat
@echo off
chcp 65001 >nul
tasklist | findstr /i "uvicorn" >nul && (echo 服务已在运行 & pause & exit /b)
cd /d %~dp0
py -m uvicorn src.main:app --host 0.0.0.0 --port 7863 || python -m uvicorn src.main:app --host 0.0.0.0 --port 7863
pause
```

=== FILE: data/source_map.json ===

```json
{
  "产品手册v3.md": "《陶粒墙板产品手册》"
}
```

=== FILE: data/sensitive_words.txt ===

一行一词，先放占位 3~5 个，注释说明由业主补充。

## 2. 关键约束

1. **技术栈红线**：禁止 MongoDB；禁止 npm/React/Vue 构建链；禁止 Docker
2. **白名单硬校验**：任何检索只允许 `ako_taoli_web_arch`，collection 名不接受请求传入
3. **Embedding 一致性**：启动校验 `collection.metadata.embedding_model_version == settings.embed_model`，不一致拒绝启动（若发布集实际由 nomic-embed-text 嵌入，以发布集 metadata 为准改配置）
4. **ChromaDB 版本**：与 `D:\AKO_Hub` 环境钉同一版本，防 `chroma.sqlite3` 读写不兼容
5. **检索参数**：加权 RRF（dense 0.7 / sparse 0.3，k=60），Top-5，阈值 0.6 起步；融合分归一化后再比阈值
6. **上下文预算**：检索 ≤2000 token、历史 ≤3 轮（按 Ollama 默认 num_ctx=4096 最短板设计）
7. **Ollama 降级**：10s 超时即降级，不阻塞；全级失败走 degraded 话术转留资
8. **脱敏**：内部文件名/编号不出网关；日志不记留资字段
9. **硬件预算**：AMD 5500U / 16GB / 无 CUDA，网关单进程 <500MB
10. **不改旧系统**：不动 `D:\AKO_Hub`、`D:\AKO_knowledge` 内任何已有 Collection 与代码

## 3. 输出要求

### 3.1 文件清单
§1.1 全部 16 个文件/目录，逐个创建，可直接 `start.bat` 启动。

### 3.2 函数签名验收表

| 函数 | 签名 | 验收点 |
|---|---|---|
| `route` | `(question: str) -> Literal["lead","chitchat","faq"]` | 三类关键词命中正确 |
| `PublishRetriever.query` | `(question: str) -> list[RetrieveItem]` | 返回 ≤5 条，score 归一化 0~1，含 dense/sparse 分 |
| `PublishRetriever.refresh` | `() -> None` | 重建 BM25 不影响在途查询 |
| `build_messages` | `(question, docs, history) -> list[dict]` | 文档总量 ≤2000 token 近似值，历史 ≤3 轮 |
| `chat_stream` | `(question, docs, history) -> AsyncIterator[str]` | P0 失败自动切 P1，P1 失败切 P2，全失败抛 `AllLLMFailed` |
| `mask` | `(internal_source: str) -> str` | 未命中返回《阿格产品资料》 |
| `save_lead` | `(lead: LeadRequest) -> None` | leads.jsonl 追加一行合法 JSON |
| `log_qa` | `(record: dict) -> None` | 含 ip/ua 三元组，无 phone 字段 |

### 3.3 冒烟测试 5 项（tests/smoke_test.py 逐项断言）

1. `GET /health` → 200，`doc_count > 0`，含 `kb_updated_at`
2. 命中问题（"陶粒墙板的隔音效果如何？"）→ SSE 正常流式，含 `[1]` 引用，`sources[*].display_name` 不含内部编号
3. 无关问题（"今天天气怎么样"）→ `action` 为 `lead/refuse/chitchat` 之一，不消耗 LLM 或走兜底
4. `POST /api/lead` 合法载荷 → `{"ok":true}`，`leads.jsonl` 新增一行
5. 流式读取中途关闭连接 → 当日日志出现 `aborted=true`，`/health` 仍 200

**Phase 1 通过标准**：5 项全 PASS。
