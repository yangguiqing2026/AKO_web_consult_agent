# 任务：墙板数据链路去隧道化 —— 结构化问答直连虚拟主机，清理 frp/cpolar 依赖

> 用法：本文件整篇贴给 VS Code Copilot Chat。
> 依据文档：AGE-TECH-AKO-DB-001 v1.0、AGE-TECH-AKO-DB-002 v1.0、AGE-TECH-AKO-WEB-001 §7。
> 前置状态：DB-002 已完成（网关 struct_* 直答 19/19 验收过）；墙板库在线 `http://wh-nc6lcdplh894m2oe8v0.my3w.com/wall_api.php`。
> 业主决策（2026-07-18）：**墙板数据链路彻底不需要内网穿透**；方案 C 上云待小程序落地后再启动。

## 0. 结论前置

墙板数据（型号/参数/报价/FAQ）的对外服务**全部由虚拟主机承担**：`wall_api.php` 公网可达、只读、7×24。本次改造做三件事：

1. **清隧道**：`D:\AKO_web_consult` 内与墙板数据相关的 frp/cpolar/隧道配置与启动项全部移除或注释，数据链路对隧道零依赖
2. **客服窗直连**：`consult_widget.html` 的结构化问答（规格/参数/报价/FAQ）改为**浏览器直连 `wall_api.php`**，不再依赖本地网关；只有 RAG 长文问答才走网关，网关不可达时优雅兜底
3. **文档对齐**：部署注释/README 明确——隧道仅保留给未来 RAG 网关内测（方案 B），与墙板数据无关

**增量改造，不重写**：网关侧 DB-002 的 struct_* 分支一行不动（本地演示仍走网关），test_walldb 8 项回归必须仍全过。

## 1. 实现清单

### 1.1 清理隧道依赖（全局检索后处理）

全项目检索 `frp|cpolar|ngrok|隧道|穿透|tunnel`，逐个判定：

- 与墙板数据链路相关（如把 wall 数据经隧道暴露的注释/脚本）→ **删除或注释**，替换为注释：`# 墙板数据走虚拟主机公网接口 wall_api.php，无需隧道（2026-07-18 起）`
- 与 RAG 网关方案 B 相关（保留给未来内测）→ 保留，但移到 `deploy\tunnel\` 子目录并在 README 注明「仅方案 B 内测用，日常不需要」
- `start.bat`：移除任何隧道启动行；网关启动脚本只起 uvicorn

### 1.2 客服窗改造：`src/static/consult_widget.html`

新增**前端本地路由层**（纯 JS，零依赖，先于网关调用执行）：

```javascript
const WALL_API = 'http://wh-nc6lcdplh894m2oe8v0.my3w.com/wall_api.php'; // 备案域名落地后改 https

// 结构化问题关键词路由（与网关 R010/R020/R030 口径一致）
const STRUCT_RULES = [
  { re: /厚度|规格|型号|多厚|尺寸|板幅/, type: 'panels'  },
  { re: /隔声|隔音|耐火|防火|抗风|水密|气密|放射|环保|吊挂|软化|防潮|保温|传热|检测/, type: 'specs' },
  { re: /价格|报价|多少钱|造价|单价/,   type: 'pricing' },
];

let _faqCache = null;   // FAQ 拉取一次，会话内缓存

async function structAnswer(text) {
  // 1) 关键词命中结构化问题 → 直连 wall_api 取数组装回答
  const rule = STRUCT_RULES.find(r => r.re.test(text));
  // 2) 未命中 → 试 FAQ 匹配（与 faq.question 最长公共子串 ≥2 字）
  // 3) 都未命中 → 返回 null，由调用方走网关 RAG
}

async function ask(question) {
  const local = await structAnswer(question);
  if (local) { renderAnswer(local, '《阿格墙板数据库》'); return; }
  // RAG 长文问题 → 网关（本地演示）；不可达 → 兜底留资
  try { await chatViaGateway(question); }          // 既有 SSE 逻辑
  catch { renderFallback('该问题已记录，请留下联系方式，顾问稍后为您解答'); }
}
```

**组装红线**（与 DB-002 网关侧完全一致）：

1. `data_status=实测` → 数值后附报告编号；`呈报` → 注明「技术说明口径」；`待补/待确认` 不展示，末尾加「其余厚度参数检测中」
2. 报价必须带「起」+「最终按厚度、构造、订单量、项目地报价」+ 留资引导
3. 禁止出现成本、毛利、内部编号
4. **跨厚度挪用禁止**：问 140mm 只展示 140 查询返回的行，绝不补 120mm 实测数

FAQ 匹配命中时直接输出 `answer` 字段原文；报价卡追加留资按钮（复用既有留资卡片）。

### 1.3 网关侧（D:\AKO_web_consult\src）——只加注释，不改行为

- `wall_query.py` 头部注释追加：`# 生产环境访客侧由虚拟主机 wall_api.php 直接承担（DB-003）；本模块供本地网关演示与内网 Agent 使用`
- `main.py`、`intent_rules.json`、`wall_answer.py` **零改动**

### 1.4 文档对齐

`D:\AKO_web_consult\README.md`（没有则新建）增补一节：

```markdown
## 部署现状（2026-07-18）
- 墙板数据库 / wall_api.php / wall.php 展示页：虚拟主机 7×24（无需隧道、无需网关）
- 客服窗结构化问答：浏览器直连 wall_api.php，网关不开也能答
- RAG 长文问答：本地网关 :7863（演示用）；正式上云（方案 C）待小程序落地后启动
- frp/cpolar 隧道：仅 deploy\tunnel\ 留存，方案 B 内测备用，日常不启动
```

## 2. 关键约束

1. **回归红线**：`tests\test_walldb.py` 8 项重跑全过；网关代码零行为变更
2. **直连红线**：widget 结构化取数只 GET `wall_api.php`；失败即走 FAQ/兜底，不得重试风暴（每问最多 1 次请求，FAQ 会话级缓存）
3. **口径红线**：实测附编号 / 呈报注明 / 价格带「起」/ 待补不展示 / 跨厚度挪用禁止——与 DB-002 逐条一致
4. **隧道零依赖**：改完后，关闭网关、关闭一切隧道进程，官网结构化问答必须全部可用
5. 接口基址常量集中在 widget 顶部 `WALL_API`，备案域名落地后只改这一行 + 网关 `.env`

## 3. 验收 6 项

1. **关网关 + 无隧道进程**：widget 问「你们墙板多厚」「多少钱一平」「120 的隔声」→ 全部直答，source=《阿格墙板数据库》
2. 「140mm 的检测报告」→ 只显示呈报口径通用数据 +「140mm 实测参数检测中」，**无 120mm 实测数混入**
3. 未命中问题（如「施工工法视频有吗」）且网关不可达 → 留资兜底话术，控制台无未捕获异常
4. 开网关 → 长文问题走 `/api/chat` SSE 流式正常
5. 全项目再搜 `frp|cpolar|ngrok|隧道|穿透`：墙板数据链路零残留（`deploy\tunnel\` 内除外）
6. **回归**：test_walldb 8 项全过

**通过标准**：1~6 全过。
