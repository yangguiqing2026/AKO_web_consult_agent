# 任务：AKO_web_consult Phase 3 —— 上线（发布流水线 / 云端镜像 / 监控告警 / 应急隧道 / 官网嵌入）

> 用法：Phase 2 验收全过后，本文件整篇贴给 VS Code Copilot Chat。
> 依据文档：AGE-TECH-AKO-WEB-001《AKO 网站咨询系统架构白皮书》v1.0.0 §7、§9。
> 前置状态：本地网关 :7863 已业务化运行；发布集 `ako_taoli_web_arch` 有内容。
> 说明：本 Phase 含云端部署，脚本类交给 Copilot 写，**云上操作步骤写进 RUNBOOK 由人执行**。

## 0. 结论前置

交付一条**发布流水线**（内部库精选 → 重嵌入 → 发布集 → 快照推送云端镜像）+ 云端部署套件（nginx 反代 / HTTPS / 守护）+ 监控告警（健康 + 同步延迟）+ frp 应急隧道 + 官网嵌入代码。核心红线：**内部库永不上云，云端只跑发布集只读镜像**。验收 = §3.3 全过 + Phase 1/2 回归全过。

## 1. 实现清单（逐项打勾）

### 1.1 新增文件

```
D:\AKO_web_consult\
├── scripts\
│   ├── publish_pipeline.py     # 内部库 → 发布集（含 --dry-run）
│   ├── sync_mirror.py          # 发布集快照 → 云端镜像
│   └── monitor.py              # 健康 + 同步延迟监控 + webhook 告警
├── deploy\
│   ├── nginx.conf.example      # 云服务器反代 + HTTPS
│   ├── frps.ini.example        # 云端隧道端
│   ├── frpc.ini.example        # 本地隧道端
│   ├── ako_web_consult.service.example   # systemd 守护（Linux 云主机）
│   └── RUNBOOK.md              # 部署/切换/回滚手册（人执行）
├── web\
│   └── embed_snippet.html      # 官网嵌入代码（交给建站方）
└── tests\test_phase3.py        # Phase 3 本地可测项
```

=== FILE: data/publish_manifest.json ===

业主维护的发布清单（发布流水线的输入）：

```json
{
  "manifest_version": "2026-07-17",
  "items": [
    { "source_collection": "ako_taoli_general_arch", "doc_id": "产品手册v3.md", "display_name": "《陶粒墙板产品手册》" }
  ]
}
```

约束：只有列进 manifest 的文档才允许进发布集——**白名单制度，不是黑名单**。

=== FILE: scripts/publish_pipeline.py ===

发布流水线（本地执行）：

```python
def load_manifest(path) -> dict
def collect_docs(manifest) -> list[dict]        # 按 source_collection + doc_id 从内部 ChromaDB 读原文
def publish(docs, dry_run: bool) -> dict         # 返回 {"added":n,"updated":n,"removed":n,"unchanged":n}
```

流程：
1. 读 manifest → 从内部各 Collection 按 doc_id 拉取**文档原文**（只读，禁止写内部库）
2. 用 `settings.embed_model`（Ollama）逐篇重嵌入 —— **不复用内部库向量**（向量空间以发布集锁定模型为准）
3. 写入/更新发布集 `ako_taoli_web_arch`；manifest 之外的历史文档标记移除
4. 同步写 `data\source_map.json`（doc_id → display_name）
5. 更新 collection metadata：`embedding_model_version`、`kb_updated_at=今天`、`manifest_version`
6. `--dry-run`：只打印将发生的增删改，不写库
7. 幂等：同一 manifest 重复执行，第二次应全部 `unchanged`

CLI：`python scripts\publish_pipeline.py --dry-run` / `--execute`

=== FILE: scripts/sync_mirror.py ===

发布集 → 云端镜像同步：

1. 从发布集导出快照 `snapshot_YYYYMMDD_HHMM.jsonl`（每行：doc_id, text, display_name）
2. HTTPS POST 到云端网关 `https://{cloud_domain}/api/admin/sync`（Header 带 `admin_token`），**全文快照 + manifest_version**
3. 云端处理（见 §1.2 网关增量）：重建临时 collection → 校验文档数与 metadata → **原子切换**（旧 collection 保留 1 代可回滚）→ 返回新的 `kb_updated_at`
4. 本地校验云端返回版本与本地一致，不一致则报错退出码非零
5. 同步记录追加 `logs\sync.log`

CLI：`python scripts\sync_mirror.py --target https://consult.example.com`

=== FILE: src/main.py 增量（云端镜像模式） ===

config 增加：
```python
mirror_mode: bool = False          # 云端为 True
cloud_admin_token: str = ""        # 与 admin_token 分开，云端专用
```

- `POST /api/admin/sync`（仅 `mirror_mode=True` 时注册）：
  - 鉴权：`cloud_admin_token`；**只接受 HTTPS**
  - 收快照 → 临时 collection `ako_taoli_web_arch_staging` 重建（嵌入用云端同版本 Ollama 或随快照传向量——**默认随快照传向量**，云端无显卡也可跑，省掉云端嵌入）
  - 校验：文档数 > 0、metadata 齐全 → 原子切换：现役 → `_backup`，staging → 现役；保留 1 代备份
  - 触发 `retriever.refresh()`；返回新 `kb_updated_at`
- `GET /embed.js`：输出官网嵌入脚本（注入 iframe 指向云端网关 `/`，带官网域名 referrer 记录）
- 镜像模式红线：`mirror_mode=True` 时**禁止任何写内部库路径的配置**，chroma_root 指向云端本地目录（如 `/opt/ako_web/chroma`）

=== FILE: scripts/monitor.py ===

监控告警（本地计划任务，每 10 min 一轮）：

1. GET 本地 `/health` + 云端 `/health`
2. 告警条件（命中即发）：
   - 任一 `ok=false` 或超时 5s 无响应（云端连续 2 轮失败才告警，防抖动）
   - **同步延迟**：云端 `kb_updated_at` 落后本地 > 2 天
   - 云端镜像模式未开启（配置漂移）
3. 告警通道：`alert_webhook_url`（企业微信/Server酱 通用 webhook，POST JSON `{title, content, ts}`）；无 webhook 时写 `logs\alert.log`
4. 每轮结果落 `logs\monitor-YYYYMMDD.jsonl`

=== FILE: deploy/nginx.conf.example ===

- 443 SSL（certbot 占位路径）→ `proxy_pass http://127.0.0.1:7863`
- SSE 必需：`proxy_buffering off; proxy_read_timeout 300s;` + `X-Accel-Buffering: no`
- 80 → 443 跳转；只暴露 443

=== FILE: deploy/frps.ini.example / frpc.ini.example ===

应急隧道（方案 B）：云服务器跑 frps（bind 7000，token 鉴权），本地跑 frpc 把 `127.0.0.1:7863` 映射为云端 `127.0.0.1:17863`；nginx 增加一个**注释掉的备用 upstream** 指向 17863，切换 = 取消注释 + reload。

=== FILE: deploy/ako_web_consult.service.example ===

systemd unit：`WorkingDirectory=/opt/ako_web_consult`，`ExecStart=venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 7863`，`Restart=always`，内存限额 `MemoryMax=800M`。

=== FILE: deploy/RUNBOOK.md ===

人执行手册（Copilot 写成傻瓜式步骤，含每步验证命令）：

1. **云服务器初始化**：2C2G / Ubuntu / Python 3.11 venv / 上传代码与 requirements / 防火墙只开 443
2. **首部署**：`.env` 配 `mirror_mode=true` + `cloud_admin_token` → systemd 启动 → nginx + certbot → `curl https://域名/health` 验证
3. **首次灌数**：本地 `publish_pipeline.py --execute` → `sync_mirror.py --target 域名` → 云端 `/health` 的 `doc_count` 与本地一致
4. **日常更新**：改 manifest → dry-run 确认 → execute → sync → 云端验证
5. **应急切换（C 宕机 → B 隧道）**：确认本地网关在线 → 启动本地 frpc → 云服务器 nginx 取消备用 upstream 注释 + reload → 验证；恢复后反向切回
6. **回滚**：同步出错 → 调 `/api/admin/sync` 重推；镜像损坏 → 手动把 `_backup` collection 改名切回
7. **密钥管理**：所有 token/密钥只放 `.env`，`deploy/` 与 `web/` 下示例文件不含真实值

=== FILE: web/embed_snippet.html ===

交给建站方的嵌入代码 + 注释说明：

```html
<!-- 阿格建筑在线咨询：放在 </body> 前 -->
<script src="https://{云端域名}/embed.js" async></script>
```

=== FILE: tests/test_phase3.py ===

本地可测项（真实云上步骤按 RUNBOOK 人工验证，见 §3.3 标注）：

1. `publish_pipeline.py --dry-run` 输出增删改计数且不写库
2. 同一 manifest 连续 `--execute` 两次 → 第二次全 `unchanged`（幂等）
3. 发布后 `source_map.json` 与 manifest 的 display_name 一致；collection metadata 三项齐全
4. 本地另起 `mirror_mode=true` 实例（不同端口、独立 chroma 目录）→ `sync_mirror.py` 推送 → 镜像 `/health` 的 `doc_count`、`kb_updated_at` 与源一致 → 问答一句验证镜像可答
5. 重推同快照 → 镜像 `_backup` 生成且现役版本不变（原子切换+可回滚）
6. `monitor.py` 单轮执行：目标健康 → 无告警；故意给错端口 → `logs\alert.log` 有记录
7. `GET /embed.js` 返回 200 且含云端域名

## 2. 关键约束

1. **内部库红线**：发布流水线对内部 Collection 只读；内部库文件、内部向量**永不上云**；云端只有发布集镜像
2. **同步安全**：`/api/admin/sync` 仅 HTTPS + 独立 token；明文 HTTP 一律 403
3. **原子切换**：镜像更新必须 staging → 校验 → 切换，保留 1 代 `_backup` 可回滚；禁止直接覆盖现役
4. **密钥红线**：`.env` 不进 git/压缩包；`deploy/`、`web/` 示例文件零真实值
5. **SSE 过反代**：nginx 配置必须 `proxy_buffering off`，否则流式变整段
6. **回归红线**：本地网关 Phase 1/2 已验收行为不变；`mirror_mode` 默认 False，不影响本地
7. **云端无模型依赖**：快照默认带向量，云端不需要 Ollama 嵌入也能切镜像（LLM 生成仍走 P0/P1 云端 API，Ollama 兜底仅本地有）
8. 告警防抖：云端健康连续 2 轮失败才告警，防误报

## 3. 输出要求

### 3.1 文件清单
§1.1 全部文件 + `src/main.py`、`src/config.py` 增量；RUNBOOK 每步带验证命令。

### 3.2 函数签名验收表

| 函数 | 签名 | 验收点 |
|---|---|---|
| `publish` | `(docs, dry_run: bool) -> dict` | dry_run 零写库；返回四计数 |
| `collect_docs` | `(manifest) -> list[dict]` | 只读内部库 |
| `/api/admin/sync` | `POST (快照JSONL) -> {kb_updated_at}` | 非 HTTPS/错 token → 403；staging 校验失败不切 |
| `sync_mirror` main | `--target URL` | 版本不一致退出码非零 |
| `monitor` main | 单轮/常驻 | 告警条件与防抖符合 §1 脚本规格 |

### 3.3 Phase 3 验收（test_phase3.py 7 项 + RUNBOOK 人工 6 项）

脚本 7 项见 §1.1 文件末。RUNBOOK 人工验证（打勾制）：

1. 云端 `/health` 200 且 `mirror_mode` 生效（无内部库路径）
2. 云端 `doc_count` = 本地发布集 `doc_count`
3. **本地网关关机，官网咨询窗仍可正常问答**（C 方案成立的核心判据）
4. 停掉云端网关 → nginx 切 frp 备用 upstream → 官网恢复应答（B 应急成立）
5. 官网页面嵌入 `embed_snippet.html` → 咨询窗正常弹出、流式正常（非整段）
6. 监控告警真实触发一次（拔云端服务）→ webhook/alert.log 收到

**Phase 3 通过标准**：脚本 7 项 + 人工 6 项全过 + Phase 1/2 回归全过。
