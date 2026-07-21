# AKO 网站咨询系统架构白皮书

**文档编号**: AGE-TECH-AKO-WEB-001
**版本**: v1.0.0
**编制日期**: 2026-07-17
**适用范围**: AMD Ryzen 5 5500U / 16GB RAM / 无 CUDA
**关联系统**: AKO_Hub（AGE-TECH-AKO-HUB-001）、chat_rag（`D:\chat_rag` 底座）
**排期说明**: 不绑定市场技术交流会时间节点，Phase 划分仅按技术依赖关系推进

---

## 1. 结论前置（执行摘要）

| 项 | 结论 |
|---|---|
| **目标** | 官网/展会场景访客在线咨询，基于阿格知识库自动应答；低置信与商机问题转留资 |
| **核心决策** | ① **发布集隔离**（`ako_taoli_web_arch`，网站只查它）② **意图前置路由**（FAQ/留资/闲聊三分）③ **加权 RRF 双路检索**（Dense 0.7 + Sparse 0.3，去 ColBERT）④ **LLM 三级路由 + 静态兜底** |
| **性能锚定** | 单次检索 < 300ms；流式首字 < 3s（云端 LLM）；并发 4~8（云端链路）/ 1~2（Ollama 兜底，超时降级不阻塞） |
| **部署规格** | 单进程 FastAPI + uvicorn，端口 **7863**，内存 < 500MB |
| **上下文预算** | 按最短板 **Ollama num_ctx=4096** 设计：检索文档 ≤ 2000 token，对话历史 ≤ 3 轮 |
| **向后兼容** | 不改动内部 6 个 Collection、不改 `KnowledgeHub.query()` 返回结构；发布集独立入库、独立维护 |

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│  官网页面（任意静态站 / CMS）                             │
│  └─ 悬浮咨询窗 consult_widget.html                       │
│     （纯 HTML + Tailwind CDN + marked.js，AKO 暖金色系）  │
└──────────────────────┬──────────────────────────────────┘
                       │  POST /api/chat（SSE 流式）
                       ▼
┌─────────────────────────────────────────────────────────┐
│  咨询网关 AKO_web_consult（FastAPI，:7863，单进程）        │
│  ├─ 限流/敏感词     10 次/min/IP                          │
│  ├─ 会话管理        session_id，TTL 30min，≤10 轮          │
│  ├─ 意图路由        FAQ检索 / 留资卡片 / 闲聊收敛           │
│  ├─ 检索器          加权 RRF：Dense 0.7 + Sparse 0.3，k=60 │
│  ├─ 上下文装配      检索 ≤2000 token + 历史 ≤3 轮          │
│  ├─ LLM 路由        P0→P1→P2→P3                           │
│  └─ 来源脱敏        内部编号 → 对外显示名                   │
└──────┬────────────────────────────┬─────────────────────┘
       │                            │
       ▼                            ▼
┌───────────────────┐      ┌──────────────────────────┐
│ ChromaDB           │      │ LLM 三级路由               │
│ ako_taoli_web_arch │      │ P0 MiniMax abab6.5s-chat  │
│ (chroma_root =     │      │ P1 Kimi    kimi-latest    │
│  D:\AKO_knowledge) │      │ P2 Ollama  qwen2.5        │
│ ▲ kb_embedder 入库 │      │    （10s 超时 → 降级话术） │
│   + 模型版本锁      │      │ P3 静态话术（转留资）      │
└───────────────────┘      └──────────────────────────┘
       │
       ▼
问答日志 / 留资 → hub_meta.db（脱敏后回流，供 GEO 与产品迭代分析）
```

---

## 3. 模块定义

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `main.py` | FastAPI 入口；路由注册；限流中间件 | 端口 7863；单进程；内存 < 500MB |
| `config.py` | 全局配置（pydantic-settings） | 阈值、权重、预算全部可配置，阈值支持热调 |
| `models.py` | Pydantic v2 数据契约 | 与接口契约（§5）逐字段一致 |
| `intent_router.py` | 意图三分：FAQ / 留资 / 闲聊 | Phase 1 关键词规则；Phase 2 可配置规则表 |
| `retriever.py` | 发布集检索 + 加权 RRF 融合 | 仅查白名单 Collection；启动校验 embedding 版本 |
| `llm_router.py` | P0→P1→P2→P3 级联，流式输出 | temperature=0.3；Ollama 10s 超时即降级 |
| `session.py` | 内存会话表 | TTL 30min；≤10 轮；日志记 session+IP+UA 三元组 |
| `source_mask.py` | 来源脱敏映射 | 未命中映射时显示默认名《阿格产品资料》 |
| `lead_card.py` | 留资卡片生成与落盘 | Phase 1 写 `leads.jsonl`；Phase 2 入 hub_meta.db |
| `logger.py` | 问答日志（JSONL） | 含分数、命中来源、动作类型、中断标记 |

---

## 4. 数据流

1. 访客提问 → 限流/敏感词检查（命中 → 固定话术）
2. 会话恢复/新建（`session_id`，超 10 轮截断最早历史）
3. **意图路由**：
   - 价格/合作/参观 → **留资卡片**（姓名+电话+意向市场：城市更新/文旅民宿/乡村民居）
   - 无关闲聊 → 固定收敛话术（不消耗 API 额度）
   - 其余 → 进入检索
4. **检索**：Dense（Ollama 嵌入 → ChromaDB Top-20）+ Sparse（内存 BM25 Top-20）→ 加权 RRF → Top-5
5. **阈值判断**：最高分 < 0.6 → 留资兜底（"稍后专属顾问联系您"）
6. **上下文装配**：检索文档总量 ≤ 2000 token + 最近 3 轮历史 + 系统提示词
7. **生成**：P0 MiniMax 流式；失败 → P1 Kimi；失败 → P2 Ollama（10s 超时）；全失败 → P3 静态话术转留资
8. **来源脱敏**后随流输出，引用标注 `[1]` `[2]`
9. 日志落盘（含 `user_aborted` 中断标记）

---

## 5. 接口契约

### 5.1 `POST /api/chat`（SSE 流式）

请求：
```json
{ "question": "陶粒墙板的隔音效果如何？", "session_id": "可选，缺省新建" }
```

SSE 事件序列：
```
event: meta     data: {"session_id":"...","action":"answer","sources":[{"display_name":"《陶粒墙板产品手册》","score":0.83}]}
event: delta    data: {"text":"陶粒墙板的..."}        // 逐段推送
event: done     data: {"score":0.83}
```

非流式动作（留资/拒答/降级）直接返回 JSON：
```json
{ "session_id":"...", "action":"lead|refuse|degraded", "answer":"...", "sources":[], "score":0.0 }
```

### 5.2 `POST /api/lead`

```json
{ "name":"张工", "phone":"138xxxx", "market":"城市更新|文旅民宿|乡村民居", "message":"可选" }
```
响应：`{ "ok": true }`；落盘 `leads.jsonl`。

### 5.3 阈值热调（运维接口，免重启）

```
GET  /api/admin/threshold?token=xxx   → {"score_threshold":0.6}
POST /api/admin/threshold             → {"token":"xxx","score_threshold":0.68}
```

### 5.4 `GET /health`

```json
{ "ok": true, "collection": "ako_taoli_web_arch", "doc_count": 28, "kb_updated_at": "2026-07-17", "embedding_model": "bge-m3" }
```

### 5.5 检索结果内部结构

与 `KnowledgeHub.query()` 返回结构保持同名字段，避免未来切回 Hub 统一检索时改下游：
```json
{ "text":"...", "source":"内部文件名", "score":0.83, "dense_score":0.81, "sparse_score":0.86 }
```

### 5.6 `source_map.json`（脱敏映射）

```json
{
  "AKO-TEST-2024-003.pdf": "《陶粒墙板检测报告（公开版）》",
  "产品手册v3.md": "《陶粒墙板产品手册》"
}
```
未命中 → 默认显示《阿格产品资料》。**内部编号、路径、版本号一律不出网关。**

---

## 6. 前端架构

- **单文件** `consult_widget.html`：悬浮气泡 + 展开对话窗，纯 HTML + Tailwind CDN + marked.js，零构建链；官网一行 `<script src=".../consult_widget.js">` 或 iframe 嵌入
- **流式渲染**：SSE `delta` 事件增量追加，marked.js 渲染 Markdown
- **留资卡片**：对话流内嵌表单（姓名/电话/意向市场三选一），提交走 `/api/lead`
- **更新日期标注**：窗口底部固定显示「知识库更新至 YYYY-MM-DD」（取自 `/health.kb_updated_at`），管理访客预期
- **AKO 暖金色系**：

| 用途 | 色值 |
|---|---|
| 窗口底色 | 奶油金 `#EBDAB9` |
| 气泡/过渡 | 冷暖灰 `#C3BEB4` |
| 边框/按钮/正文锚点 | 深棕黑 `#231E1C` |
| 点缀/成功提示 | 琥珀金 `#A08C64` |
| 标题/高亮关键词 | 熔金 `#B99B5F` |

禁忌：纯白 `#FFFFFF` 背景、冷色主导、正午白光感配色。

---

## 7. 部署架构

| 方案 | 做法 | 可用性 | 定位 |
|---|---|---|---|
| **A 本地直连** | 网站与网关同机/局域网，蒲公英组网互通 | 机器关机即断 | Phase 1 开发测试、展会现场演示 |
| **B 隧道回源** | 云服务器 nginx 反代 + frp 隧道回源本地 :7863 | 依赖本地开机 | Phase 2 内测；**正式上云后保留为应急隧道** |
| **C 云端镜像** | 轻量云服务器（2C2G）跑网关 + 发布集镜像，本地为知识源定期增量同步 | 7×24 | Phase 3 正式上线 |

推进路径：**A（跑通）→ B（内测暴露公网问题）→ C（正式）+ B 留作 C 宕机应急**。

---

## 8. 安全与边界

| 防线 | 措施 |
|---|---|
| **第一道：发布集隔离** | 网站只能查 `ako_taoli_web_arch`（白名单硬校验）；内部检测报告、未审定稿、成本数据永不进发布集 |
| **第二道：来源脱敏** | `source_map.json` 映射；未命中走默认显示名；内部编号不出网关 |
| **第三道：输入过滤** | 敏感词表 + 单 IP 10 次/min 限流 |

运行期边界：

- **Embedding 一致性锁定**：发布集 metadata 写入 `embedding_model_version`；网关启动校验本地嵌入模型与锁定值一致，**不一致拒绝启动**；升级模型必须全量重嵌发布集（不得拷贝旧向量沿用）
- **阈值校准**：0.6 起步；因链路去除了 ColBERT，分数分布相对内部三向量 `final_score` 整体漂移，内部 Agent 的 0.75 阈值**不可直接搬用**；用测试集（种子问题 50~80 条人工整理 + LLM 扩写至 200 条人工标注）校准，热调接口免重启
- **Ollama 降级**：10s 无响应 → 降级话术（"正在为您查询，稍后专属顾问联系您"）→ 留资入口；绝不让本地兜底成为阻塞点
- **流式中断**：捕获客户端断开，日志记 `user_aborted`，不报错、不留脏数据
- **上下文预算**：检索 ≤ 2000 token、历史 ≤ 3 轮，按 P2（Ollama 默认 num_ctx=4096）最短板设计；Ollama 侧可显式 `num_ctx=8192`，但预算仍按 4096 档保守执行
- **日志脱敏**：问答日志不记电话等留资字段；留资与问答分文件存储

---

## 9. 扩展路线

| Phase | 范围 | 验收 |
|---|---|---|
| **Phase 1 骨架**（本次提示词交付范围） | 发布集 + 网关全链路（白名单/加权RRF/阈值/上下文预算/LLM路由/脱敏/SSE）+ 极简前端 + 冒烟测试 | §11 冒烟 5 项全过 |
| **Phase 2 业务化** | 完整意图路由规则表、留资入 hub_meta.db、问答日志回流分析、前端品牌化完整版、200 条测试集校准阈值、敏感词库 | 测试集 Recall/Precision 达标，留资链路闭环 |
| **Phase 3 上线** | 发布流水线自动化（内部库更新→发布集增量→云端镜像同步）、方案 C 部署、监控告警、方案 B 应急隧道 | 本地关机官网可答；同步延迟可监控 |

---

## 10. 依赖清单

| 依赖 | 用途 | 备注 |
|---|---|---|
| `fastapi` + `uvicorn[standard]` | 网关 | — |
| `pydantic>=2` / `pydantic-settings` | 契约与配置 | — |
| `chromadb` | 发布集检索 | **版本与 AKO_Hub 锁定一致**（requirements.txt 钉版），防止 `chroma.sqlite3` 读写不兼容 |
| `httpx` | LLM API / Ollama 调用 | 异步流式 |
| `rank_bm25` + `jieba` | 稀疏检索 | 发布集 ≤100 篇，内存索引 |
| 前端 | Tailwind CDN + marked.js | 零 npm、零构建链 |

**明确排除**：不使用 MongoDB（技术栈为 ChromaDB/Qdrant）；不引入 React/Vue 构建链；不引入 Docker。

---

## 11. 文件清单

```
D:\AKO_web_consult\
├── src\
│   ├── main.py                 # FastAPI 入口（:7863）
│   ├── config.py               # pydantic-settings 全局配置
│   ├── models.py               # Pydantic v2 契约
│   ├── intent_router.py        # 意图三分（Phase 1 关键词规则）
│   ├── retriever.py            # 发布集检索 + 加权 RRF
│   ├── llm_router.py           # P0→P1→P2→P3 级联流式
│   ├── session.py              # 内存会话表
│   ├── source_mask.py          # 来源脱敏（source_map.json）
│   ├── lead_card.py            # 留资卡片（leads.jsonl）
│   ├── logger.py               # 问答日志（JSONL）
│   └── static\
│       └── consult_widget.html # 悬浮咨询窗（Phase 1 极简版）
├── data\
│   ├── source_map.json         # 内部编号 → 对外显示名
│   ├── sensitive_words.txt     # 敏感词表
│   └── leads.jsonl             # 留资落盘（运行时生成）
├── logs\                       # 问答日志（运行时生成）
├── tests\
│   └── smoke_test.py           # 冒烟 5 项
├── requirements.txt            # 钉版
└── start.bat                   # chcp 65001 + 防重复启动
```

**冒烟测试 5 项验收标准**：

1. `GET /health` → 200，返回 `doc_count` 与 `kb_updated_at`
2. KB 命中问题 → SSE 流式输出，含 `[1]` 引用，`sources` 为**对外显示名**
3. 低于阈值问题 → `action=lead`，返回留资话术
4. `POST /api/lead` → 200，`leads.jsonl` 新增一行
5. 流式输出中途关闭连接 → 日志记 `user_aborted`，进程无异常、可继续服务

---

## 附录 A：评审意见处置记录

| 处置 | 评审意见 | 本版处理 |
|---|---|---|
| ✅ 采纳 | Embedding 版本锁定 | §8「Embedding 一致性锁定」，启动校验拒绝不一致启动 |
| ✅ 采纳 | Ollama 并发瓶颈与降级 | §8「Ollama 降级」，10s 超时 + 降级话术 + 留资 |
| ✅ 采纳 | 上下文长度爆炸风险 | §1/§8 上下文预算，按 num_ctx=4096 最短板设计 |
| ✅ 采纳 | 流式中断处理 | §8「流式中断」 |
| ✅ 采纳 | 发布-镜像时间差 | §6 前端固定标注知识库更新日期 |
| ✅ 采纳 | 会话跨 Tab 去重 | §3 日志记录 session+IP+UA 三元组 |
| ✅ 采纳 | 方案 B 保留为应急 | §7 部署路径 |
| ❌ 修正 | Phase 1 清单使用 MongoDB 语法（`db.createCollection`） | 技术栈为 ChromaDB，发布集走 kb_embedder 入库；§10 明确排除 MongoDB |
| ❌ 修正 | 「RRF k 值调节 Dense/Sparse 权重」（技术错误） | k 为对称平滑常数；权重分配改用加权 RRF（0.7/0.3），§3/§4 |
| ❌ 修正 | 「从历史咨询日志抽 200 条问题」（数据源不存在） | 改为种子问题人工整理 + LLM 扩写，§8「阈值校准」 |
| ⚠️ 排除 | 「月底展会前跑通」排期 | 按业主指示不纳入排期约束 |
| ➕ 补充 | 评审漏项：Ollama 默认 num_ctx=4096 为上下文最短板 | §8 上下文预算据此设计 |
