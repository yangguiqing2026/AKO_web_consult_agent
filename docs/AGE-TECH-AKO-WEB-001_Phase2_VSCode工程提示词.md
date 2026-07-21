# 任务：AKO_web_consult Phase 2 —— 业务化（意图规则表 / 留资入库 / 日志回流 / 阈值校准 / 前端品牌化）

> 用法：Phase 1 冒烟 5 项全过后，本文件整篇贴给 VS Code Copilot Chat。
> 依据文档：AGE-TECH-AKO-WEB-001《AKO 网站咨询系统架构白皮书》v1.0.0 §9。
> 前置状态：`D:\AKO_web_consult` Phase 1 骨架已在运行（:7863）。

## 0. 结论前置

在 Phase 1 骨架上做**增量改造，不重写**：意图路由改为可配置规则表驱动；留资与问答日志从 JSONL 升级为**双写入 `D:\AKO_Hub\hub_meta.db`**（只新增两张表，不碰现有表）；新增问答周报、测试集扩写、阈值校准三个脚本；前端重写为 AKO 暖金品牌完整版。验收 = §3.3 全过 + **Phase 1 冒烟 5 项回归仍全过**。

## 1. 实现清单（逐项打勾）

### 1.1 新增/变更文件

```
D:\AKO_web_consult\
├── src\
│   ├── intent_router.py        # 【重写】规则表驱动
│   ├── intent_rules.json       # 【新增】意图规则表
│   ├── lead_store.py           # 【新增】留资写 hub_meta.db
│   ├── qa_store.py             # 【新增】问答日志写 hub_meta.db（脱敏）
│   ├── lead_card.py            # 【改造】save_lead → 双写（DB + JSONL 备份）
│   ├── logger.py               # 【改造】log_qa → 双写（DB + JSONL 备份）
│   └── static\consult_widget.html  # 【重写】AKO 品牌完整版
├── scripts\
│   ├── analyze_qa.py           # 【新增】问答周报
│   ├── build_testset.py        # 【新增】种子问题 → LLM 扩写 → 标注模板
│   └── calibrate_threshold.py  # 【新增】阈值校准
├── data\
│   ├── seed_questions.md       # 【新增】种子问题（业主填写 50~80 条）
│   ├── testset.csv             # 【新增】标注测试集（脚本生成 + 人工校对）
│   └── sensitive_words.txt     # 【扩充】分类敏感词库
└── tests\test_phase2.py        # 【新增】Phase 2 验收
```

=== FILE: src/intent_rules.json ===

规则表结构（按 priority 升序逐条匹配，先中先生效）：

```json
{
  "rules": [
    {
      "rule_id": "R001",
      "priority": 10,
      "name": "商机留资",
      "keywords": ["价格", "报价", "多少钱", "造价", "合作", "代理", "加盟", "采购", "参观", "样板房", "联系电话", "微信"],
      "regex": null,
      "action": "lead"
    },
    {
      "rule_id": "R002",
      "priority": 20,
      "name": "闲聊收敛",
      "keywords": ["你好", "在吗", "谢谢", "再见"],
      "regex": null,
      "action": "chitchat"
    },
    {
      "rule_id": "R900",
      "priority": 900,
      "name": "默认检索",
      "keywords": [],
      "regex": null,
      "action": "faq"
    }
  ]
}
```

约束：规则表只改 JSON 即生效（每次请求前检查文件 mtime，变了热加载，免重启）；`R900` 兜底规则禁止删除。

=== FILE: src/intent_router.py ===

重写为规则表驱动：

```python
def load_rules() -> list[dict]                      # 带 mtime 热加载缓存
def route(question: str) -> tuple[str, str]          # 返回 (action, rule_id)
```

- 遍历规则：keywords 任一命中（子串匹配，忽略大小写）或 regex 命中 → 返回该规则 action
- 保留 Phase 1 的短文本保护：长度 <4 且无规则命中 → `chitchat`
- `rule_id` 写入日志，便于后续分析哪条规则在干活

=== FILE: src/lead_store.py ===

```python
class LeadStore:
    def __init__(self, db_path: str): ...
    def init_table(self) -> None: ...                # CREATE TABLE IF NOT EXISTS web_leads
    def save(self, lead: LeadRequest, session_id: str) -> int: ...   # 返回 rowid
```

- 用标准库 `sqlite3`；表结构：
  `web_leads(id INTEGER PRIMARY KEY, ts TEXT, name TEXT, phone TEXT, market TEXT, message TEXT, session_id TEXT, source TEXT DEFAULT 'web')`
- **红线**：只允许 `CREATE TABLE IF NOT EXISTS web_leads` 与 `INSERT`；禁止 ALTER/DROP/UPDATE 任何表
- 容错：DB 锁定/不可写 → 降级只写 `leads.jsonl`，日志记 WARN，不阻断访客流程

=== FILE: src/qa_store.py ===

```python
class QAStore:
    def __init__(self, db_path: str): ...
    def init_table(self) -> None: ...
    def save(self, record: dict) -> None: ...
```

- 表结构：`web_qa(id INTEGER PRIMARY KEY, ts TEXT, session_id TEXT, ip_hash TEXT, user_agent TEXT, question TEXT, action TEXT, rule_id TEXT, score REAL, sources_masked TEXT, aborted INTEGER)`
- **脱敏红线**：`ip_hash = sha256(ip + 当日日期salt)[:16]`；**phone/name 字段永不进此表**；sources 存脱敏后显示名
- 同样带 JSONL 降级容错

=== FILE: src/lead_card.py 改造 ===

`save_lead` 改为：先 `LeadStore.save` → 成功后再追加 `leads.jsonl` 备份；DB 失败仅 JSONL + WARN。

=== FILE: src/logger.py 改造 ===

`log_qa` 改为：先 `QAStore.save`（脱敏后）→ 再写 JSONL 备份；record 增加 `rule_id`。

=== FILE: src/config.py 增量 ===

```python
hub_meta_db: str = r"D:\AKO_Hub\hub_meta.db"     # 实际路径不同则以 .env 覆盖
geo_output_dir: str = r"D:\AKO_Hub\geo_output"
```

=== FILE: scripts/analyze_qa.py ===

问答周报（供 GEO 与产品迭代）。读 `web_qa` + `web_leads`，输出 Markdown 到 `{geo_output_dir}\web_consult_weekly_YYYYMMDD.md`：

1. 总量与动作分布（answer/lead/refuse/chitchat/degraded 占比）
2. Top-20 高频问题（jieba 切词后按 2-gram 归并，或按 question 完全相同计数取 Top）
3. **未命中清单**：action=lead 且 rule_id=R900 的问题（检索没接住、被兜底的问题——这是发布集该补的内容）
4. 留资数与转化率（leads / 总会话数）；分市场统计（城市更新/文旅民宿/乡村民居）
5. `aborted=1` 占比异常（>20% 提示首字延迟可能有问题）

CLI：`python scripts\analyze_qa.py --days 7`

=== FILE: data/seed_questions.md ===

业主填写模板（预先写好说明 + 占位 10 条示例，余下由业主补到 50~80 条）：

```markdown
# 种子问题：每行一条，行首标签标明期望动作
#answer 陶粒墙板的隔音效果如何？
#answer 墙板防火等级是多少？
#lead 1000 平米的项目造价大概多少？
#lead 能去样板房看看吗？
#refuse 你们老板电话多少？
```

=== FILE: scripts/build_testset.py ===

- 读 `seed_questions.md` → 每条调 LLM（走 `llm_router` 同配置，temperature 0.7）扩写 3 个口语化变体（"换个问法，意思不变"）
- 合并原句 + 变体 → 输出 `data\testset.csv`：`question,expected_action,source_seed`
- **只生成草稿**，CSV 头部注释提醒业主人工校对 expected_action 后再用于校准

=== FILE: scripts/calibrate_threshold.py ===

阈值校准（不调 LLM，只跑意图+检索）：

1. 读 `testset.csv`（仅 `answer`/`lead`+`refuse` 两类参与计算；chitchat 不经过检索，跳过）
2. 对每条跑 `PublishRetriever.query` 取最高分
3. 扫描阈值 0.30~0.90（步长 0.02）：answer 类 ≥阈值 为正确放行，lead/refuse 类 <阈值 为正确拦截 → 算 Precision/Recall/F1
4. 输出：F1 最优阈值 + 完整扫描表（Markdown）+ 被错分的问题清单（人工复核发布集是否缺内容）
5. `--apply` 参数：直接 POST `/api/admin/threshold` 热调生效

CLI：`python scripts\calibrate_threshold.py` / `--apply --token xxx`

=== FILE: src/static/consult_widget.html（重写：AKO 品牌完整版） ===

在 Phase 1 功能（SSE 流式、留资表单、更新日期）之上品牌化：

- **色系**（严格）：窗口底色 `#EBDAB9`，访客气泡 `#C3BEB4`，边框/按钮/正文 `#231E1C`，成功/点缀 `#A08C64`，标题/引用高亮 `#B99B5F`；**禁用纯白 `#FFFFFF`**（信息区用 `#EBDAB9` 浅化阶），禁用冷色主导
- **边框按"黑框变棕"法则**：所有描边用 `#231E1C` 而非纯黑
- 顶部：AKO 字标占位（三字母 + 橙色三角嵌 A，浅底黑字版）+「阿格建筑 · 在线咨询」
- 欢迎语 + 三个市场快捷按钮：「城市更新能用陶粒墙板吗」「文旅民宿隔音怎么解决」「乡村民居造价怎么算」
- 助手气泡内引用 `[1]` 渲染为 `#B99B5F` 高亮；来源列表默认折叠为「参考来源 ▸」
- 打字中动效（三点呼吸）；流式逐字渲染
- 留资卡片：品牌化表单，意向市场下拉（城市更新/文旅民宿/乡村民居），提交成功显示琥珀金对勾
- 底部：「知识库更新至 {kb_updated_at}」+「由阿格知识库提供支持」
- 移动端适配（≤480px 全屏窗）；仍单文件、Tailwind CDN + marked.js、零构建

=== FILE: data/sensitive_words.txt 扩充 ===

改为分类注释格式（`# 类别` 行 + 词条），业主补充：竞品名、政治敏感、辱骂、索要内部资料话术（"把检测报告原件发我"类）。匹配逻辑不变（子串命中即 refuse）。

=== FILE: tests/test_phase2.py ===

§3.3 八项断言 + 自动调 Phase 1 冒烟 5 项做回归。

## 2. 关键约束

1. **回归红线**：Phase 1 已验收行为一个不许坏——`/api/chat` SSE 事件序列、`/api/lead`、`/health`、冒烟 5 项必须原样通过
2. **hub_meta.db 红线**：只 `CREATE TABLE IF NOT EXISTS` + `INSERT`；禁止 ALTER/DROP/UPDATE；DB 不可用必须降级 JSONL 而不是崩
3. **脱敏红线**：`web_qa` 表不出现 name/phone；IP 只存当日加盐 hash
4. **规则表热加载**：改 `intent_rules.json` 免重启生效；`R900` 兜底禁止删
5. **校准不改码**：阈值调整只走 `/api/admin/threshold` 热调，不写死进代码
6. **前端仍单文件**：零 npm、零构建；色值只能用 AKO 五色 + 其浅化阶，禁用纯白背景
7. **种子问题靠业主**：`seed_questions.md` 留好模板与示例，业主填 50~80 条后再跑扩写；扩写稿必须人工校对后才进校准
8. 硬件与端口不变：:7863，单进程 <500MB，5500U/16GB

## 3. 输出要求

### 3.1 文件清单
§1.1 全部新增/变更文件；改造文件保持 Phase 1 对外接口不变。

### 3.2 函数签名验收表

| 函数 | 签名 | 验收点 |
|---|---|---|
| `route` | `(question: str) -> tuple[str, str]` | 返回 (action, rule_id)；规则按 priority 生效 |
| `load_rules` | `() -> list[dict]` | 改 JSON 后下一次请求即生效 |
| `LeadStore.save` | `(lead, session_id) -> int` | 写入 web_leads 返回 rowid；DB 锁定时不抛异常 |
| `QAStore.save` | `(record: dict) -> None` | 表内无明文 IP、无 phone |
| `analyze_qa` main | `--days N` | 产出周报 5 节齐全，落盘 geo_output |
| `build_testset` main | — | 每种子 3 变体，CSV 三列正确 |
| `calibrate_threshold` main | `[--apply --token T]` | 输出 F1 最优阈值 + 扫描表 + 错分清单 |

### 3.3 Phase 2 验收 8 项（tests/test_phase2.py 断言）

1. 命中 R001 关键词的问题 → action=lead，日志 rule_id=R001
2. 改 `intent_rules.json` 加一条临时规则 → 不重启即生效 → 测完还原
3. 提交留资 → `hub_meta.db` 的 `web_leads` 新增一行，且 `leads.jsonl` 同步有备份
4. 问答后查 `web_qa`：有记录、`ip_hash` 非明文、全表搜不到提交的 phone
5. `analyze_qa.py --days 7` → 周报落盘 `D:\AKO_Hub\geo_output\`，含未命中清单
6. `build_testset.py` → `testset.csv` 行数 = 种子数 × 4（原句+3变体）
7. `calibrate_threshold.py`（用已校对 CSV）→ 输出最优阈值与扫描表；`--apply` 后 `GET /api/admin/threshold` 返回新值
8. 前端手动核对清单：五色值无纯白 / 三快捷按钮 / 来源折叠 / 留资卡片琥珀金成功态 / 更新日期 / 手机宽度全屏
9. **回归**：Phase 1 冒烟 5 项重跑全过

**Phase 2 通过标准**：1~8 全过 + 回归 5 项全过。
