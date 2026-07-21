# 任务：WallDB 接入 —— AKO_web_consult 结构化取数通道 + AKO_Hub Agent 工具

> 用法：本文件整篇贴给 VS Code Copilot Chat（或逐 `=== FILE ===` 段粘贴）。
> 依据文档：AGE-TECH-AKO-WEB-001《网站咨询系统架构白皮书》v1.0.0、AGE-TECH-AKO-DB-001《陶粒墙板数据库部署与读取步骤》v1.0。
> 前置状态：`D:\AKO_web_consult` Phase 1/2 已在运行（:7863）；墙板库已上线，接口 `http://wh-nc6lcdplh894m2oe8v0.my3w.com/wall_api.php` 实测可用（2026-07-18 验收）。

## 0. 结论前置

墙板库的**结构化数据（型号/参数/报价/FAQ）不再走 RAG**，改为前置意图命中后直接调 `wall_api.php` 取数直答，**零 LLM 消耗、零编造空间**；未命中仍走原 RAG 链路。AKO_Hub 侧注册同款 `wall_query` 工具，全 Agent 共用同一接口、同一取数约束。验收 = §3.3 全过 + Phase 1/2 回归全过。**增量改造，不重写、不改对外既有行为。**

分工红线（与 DB-001 §8 一致）：**结构化取数走 wall_api，长文问答走 RAG**，互不替代；成本数据任何通道都不得出现。

## 1. 实现清单 A：AKO_web_consult 网关改造

### 1.1 新增/变更文件

```
D:\AKO_web_consult\
├── src\
│   ├── wall_query.py        # 【新增】wall_api 只读客户端（异步 + 60s 缓存）
│   ├── wall_answer.py       # 【新增】结构化回答组装（含报告编号/口径措辞）
│   ├── intent_rules.json    # 【改造】新增 3 条结构化规则，R001 价格类词移交
│   ├── main.py              # 【改造】/api/chat 新增 struct_* 动作分支
│   └── config.py            # 【增量】WALL_API 配置项
└── tests\test_walldb.py     # 【新增】§3.3 验收断言
```

=== FILE: src/config.py 增量 ===

```python
wall_api_base: str = "http://wh-nc6lcdplh894m2oe8v0.my3w.com/wall_api.php"  # 绑定备案域名后改 https，.env 覆盖
wall_api_timeout: float = 10.0
wall_api_cache_ttl: int = 60        # 秒；墙板数据低频变更，缓存防打爆虚拟主机
wall_api_enabled: bool = True       # False 时全部回退 RAG 链路
```

=== FILE: src/wall_query.py ===

```python
"""wall_api.php 只读客户端：异步、带 TTL 缓存、失败静默降级（返回 None）。"""
import time
import httpx
from config import settings

_cache: dict[str, tuple[float, list]] = {}

async def wall_query(qtype: str, thickness: int = 0) -> list[dict] | None:
    """返回 data 列表；接口失败/超时/未启用 → None（调用方回退 RAG，绝不阻断访客）。"""
    if not settings.wall_api_enabled:
        return None
    key = f"{qtype}:{thickness}"
    if key in _cache and time.time() - _cache[key][0] < settings.wall_api_cache_ttl:
        return _cache[key][1]
    params = {"type": qtype}
    if thickness:
        params["thickness"] = thickness
    try:
        async with httpx.AsyncClient(timeout=settings.wall_api_timeout) as c:
            r = await c.get(settings.wall_api_base, params=params)
            j = r.json()
        if not j.get("ok"):
            return None
        _cache[key] = (time.time(), j["data"])
        return j["data"]
    except Exception:
        return None   # WARN 由调用方记日志
```

=== FILE: src/wall_answer.py ===

回答组装红线：
1. `data_status=实测` → 数值后附「（报告 {report_no}）」；`呈报` → 附「（技术说明口径）」；`待补/待确认` → **不输出该行**，改为末尾一句「其余厚度参数检测中，陆续上库」
2. 报价必须带「起」字与「最终按厚度、构造、订单量、项目地报价」
3. 全文禁止出现成本、毛利、内部文档编号
4. 输出为 Markdown 纯文本，走既有 SSE `delta` 逐段推送，sources 固定显示 `《阿格墙板数据库》`

```python
def answer_panels(rows: list[dict]) -> str: ...    # 六档厚度×两类，表格或分点列出
def answer_specs(rows: list[dict], question: str) -> str: ...  # 按 question 关键词过滤相关项目后输出
def answer_pricing(rows: list[dict]) -> str: ...   # 450/400 起 + 报价说明 + 留资引导句
```

=== FILE: src/intent_rules.json 改造 ===

新增 3 条规则（priority 均高于 R001 商机留资），并把 R001 中的 `价格/报价/多少钱/造价/单价` 移交给 R030：

```json
{"rule_id":"R010","priority":8, "name":"规格型号直答","keywords":["厚度","规格","型号","多厚","板幅","尺寸","多大"],"action":"struct_panels"},
{"rule_id":"R020","priority":9, "name":"性能参数直答","keywords":["隔声","隔音","耐火","防火","抗风","水密","气密","放射性","环保","吊挂","软化","防潮","保温","传热","检测报告"],"action":"struct_specs"},
{"rule_id":"R030","priority":10,"name":"报价卡","keywords":["价格","报价","多少钱","造价","单价"],"action":"struct_pricing"}
```

- R001（商机留资）保留：合作/代理/加盟/采购/参观/样板房/电话/微信类词
- R030 报价卡回答**末尾必须带留资引导**（"留下联系方式，顾问出具正式报价"），商机链路不断
- `R900` 兜底禁止删；规则热加载机制不变

=== FILE: src/main.py 改造 ===

`/api/chat` 在意图路由后新增分支（置于 FAQ 检索之前）：

```python
if action == "struct_panels":
    rows = await wall_query("panels")
    if rows: return sse_answer(answer_panels(rows), source="《阿格墙板数据库》", action="answer")
    action = "faq"   # 接口失败 → 回退 RAG，日志记 WARN wall_api_down
elif action == "struct_specs":
    thk = extract_thickness(question)          # 从问题抓 100~200 数字，无则 0=全部
    rows = await wall_query("specs", thk)
    if rows: return sse_answer(answer_specs(rows, question), source="《阿格墙板数据库》", action="answer")
    action = "faq"
elif action == "struct_pricing":
    rows = await wall_query("pricing")
    if rows: return sse_answer(answer_pricing(rows), source="《阿格墙板数据库》", action="answer")
    action = "lead"  # 报价接口失败时退化为纯留资，不放过商机
```

`/health` 增量：`"wall_api": "up|down"`（启动时 ping 一次 `type=meta`）。

## 2. 实现清单 B：AKO_Hub Agent 接入

### 2.1 新增文件

```
D:\BaiduSyncdisk\AKO_Hub\
└── tools\
    └── wall_query.py        # 【新增】同步版工具（requests），供 7 个 Agent 注册
```

=== FILE: tools/wall_query.py（AKO_Hub 同步版） ===

```python
"""陶粒墙板库只读工具 v1.0 —— 全 Agent 共用。失败返回 None，由 Agent 走留资话术。"""
import requests

WALL_API_BASE = "http://wh-nc6lcdplh894m2oe8v0.my3w.com/wall_api.php"  # 备案域名落地后改 https

def wall_query(qtype: str, thickness: int = 0) -> list[dict] | None:
    """qtype: panels | specs | pricing | faq | projects | meta；thickness 仅 specs 用（0=全部）。"""
    if qtype not in ("panels", "specs", "pricing", "faq", "projects", "meta"):
        return None
    params = {"type": qtype}
    if thickness:
        params["thickness"] = thickness
    try:
        r = requests.get(WALL_API_BASE, params=params, timeout=10)
        j = r.json()
        return j["data"] if j.get("ok") else None
    except Exception:
        return None

# LangGraph/LangChain 工具规格（注册用）
TOOL_SPEC = {
    "name": "wall_query",
    "description": "查询阿格陶粒墙板数据库：规格型号(panels)、性能参数(specs，可按厚度mm过滤)、报价(pricing)、常见问题(faq)、项目案例(projects)。规格/参数/报价类问题必须先调本工具，禁止凭记忆回答数值。",
    "parameters": {
        "type": "object",
        "properties": {
            "qtype": {"type": "string", "enum": ["panels", "specs", "pricing", "faq", "projects", "meta"]},
            "thickness": {"type": "integer", "enum": [0, 100, 120, 140, 150, 180, 200], "default": 0}
        },
        "required": ["qtype"]
    }
}
```

=== 注册方式 ===

在各 Agent 的工具列表中按现有框架方式注册（LangGraph：`Tool.from_function(func=wall_query, name=..., description=...)`，或按 TOOL_SPEC 绑定）。**只授 `wall_query` 一个工具，不授任何写库能力。**

=== 系统提示词片段（追加到各对外 Agent 的 system prompt，原文照抄） ===

```
【墙板数据取数约束 v1.0】
1. 涉及陶粒墙板的规格、厚度、价格、性能参数、检测报告、案例、常见问题，一律先调用 wall_query 工具取数，禁止凭记忆或通用知识回答数值。
2. 引用数值时区分口径：data_status=实测 → 附报告编号；呈报 → 注明「技术说明口径」。
3. 命中 data_status=待补 / 待确认 → 不编造，答复「该数据检测中/确认中」，并引导对方留资。
4. 价格表述必须带「起」，并补「最终按厚度、构造、订单量与项目地报价，以正式报价单为准」。
5. 成本、毛利、内部文件编号、未审定内容 → 库中无此数据，统一答复「该信息不便公开，可留资由顾问对接」。
6. wall_query 返回空（接口故障）→ 不重复重试，直接走留资话术。
```

## 3. 输出要求

### 3.1 函数签名验收表

| 函数 | 签名 | 验收点 |
|---|---|---|
| `wall_query`（网关版） | `async (qtype, thickness=0) -> list[dict] \| None` | 超时/500/禁用均返回 None，不抛异常；60s 内二次调用命中缓存 |
| `answer_pricing` | `(rows) -> str` | 含 450/400、「起」、报价说明、留资引导 |
| `answer_specs` | `(rows, question) -> str` | 实测行附报告编号；呈报行注明口径；无待补行 |
| `extract_thickness` | `(question) -> int` | "120的隔声"→120；无厚度→0 |
| `wall_query`（Hub 版） | `(qtype, thickness=0) -> list[dict] \| None` | 与网关版行为一致（同步） |

### 3.2 关键约束

1. **回归红线**：Phase 1 冒烟 5 项、Phase 2 验收 8 项重跑全过；`/api/chat` SSE 事件序列不变
2. **降级红线**：wall_api 任何故障 → 回退 RAG/留资，访客侧无感，日志记 `wall_api_down`
3. **成本红线**：任何回答、任何工具、任何日志不得出现成本数据
4. **只读红线**：两个 `wall_query` 只有 GET `wall_api.php`，无任何写操作
5. 缓存 TTL 60s 可配置；接口基址走 `.env`，绑备案域名后改 https 即切换

### 3.3 WallDB 接入验收 8 项（tests/test_walldb.py + 手工）

1. 「你们墙板多厚」→ 直答六档厚度，action=answer，无 LLM 调用（日志无 llm 记录），source=《阿格墙板数据库》
2. 「120 的隔声量多少」→ 回答含 `46(-1;-4)dB` 与 `BETC-JN1-2018-00206`
3. 「多少钱一平」→ 报价卡含 450/400 起 + 报价说明 + 留资引导；且**不再**直接 action=lead
4. 「200mm 的 K 值」→ 回答注明「技术说明口径」
5. 「140mm 的检测报告」→ 回答「检测中」+ 留资引导，**无编造数值**
6. 停网/改错 `wall_api_base` → 以上问题自动回退 RAG 或留资，无 500，日志有 `wall_api_down`
7. AKO_Hub 任一 Agent 调 `wall_query("pricing")` → 返回 12 行；问「成本多少」→ 按约束第 5 条婉拒
8. **回归**：Phase 1 冒烟 5 项 + Phase 2 验收 8 项全过

**通过标准**：1~8 全过。
