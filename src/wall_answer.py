"""墙板数据库结构化回答组装 —— 含报告编号/口径措辞，零编造。"""
import re


def answer_panels(rows: list[dict]) -> str:
    """六档厚度 × 两类（承重墙板/非承重挂板），表格列出。"""
    lines = [
        "阿格陶粒墙板产品规格如下：",
        "",
        "| 型号 | 类型 | 厚度 | 最大板幅 | 防火等级 |",
        "|------|------|------|----------|----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['model_code']} | {r['panel_type']} | {r['thickness_mm']}mm "
            f"| {r['max_size']} | {r['fire_class']} |"
        )
    lines.append("")
    lines.append("如有定制需求，可留下联系方式，顾问为您提供一对一方案。")
    return "\n".join(lines)


def answer_specs(rows: list[dict], question: str) -> str:
    """按 question 关键词过滤相关项目后输出，区分实测/呈报/待补口径。"""
    # 提取问题中的关键词用于过滤
    kw_map = {
        "隔声": ["隔声"],
        "隔音": ["隔声"],
        "耐火": ["耐火"],
        "防火": ["耐火"],
        "抗风": ["抗风"],
        "水密": ["水密"],
        "气密": ["气密"],
        "放射性": ["放射性"],
        "环保": ["放射性"],
        "吊挂": ["吊挂"],
        "软化": ["软化"],
        "防潮": ["软化"],
        "保温": ["传热"],
        "传热": ["传热"],
        "检测报告": [],
        "检测": [],
        "收缩": ["收缩"],
        "冲击": ["冲击"],
        "抗弯": ["抗弯"],
        "抗冻": ["抗冻"],
        "面密度": ["面密度"],
        "含水": ["含水"],
        "配筋": ["配筋"],
        "承载力": ["抗弯"],
    }

    matched_kw = []
    for kw, targets in kw_map.items():
        if kw in question:
            matched_kw.extend(targets if targets else [kw])

    # 过滤相关项目
    if matched_kw:
        filtered = []
        for r in rows:
            item = r.get("spec_item", "")
            for kw in set(matched_kw):
                if kw in item:
                    filtered.append(r)
                    break
        if filtered:
            rows = filtered

    # 分离状态
    measured = [r for r in rows if r.get("data_status") == "实测"]
    reported = [r for r in rows if r.get("data_status") == "呈报"]
    pending = [r for r in rows if r.get("data_status") in ("待补", "待确认")]

    lines: list[str] = []

    if measured:
        lines.append("**实测数据（可出具检测报告）：**")
        lines.append("")
        for r in measured:
            val = r["spec_value"]
            unit = r.get("unit")
            report = r.get("report_no", "")
            item = r["spec_item"]
            line = f"- **{item}**：{val}"
            if unit:
                line += f" {unit}"
            if report:
                line += f"（报告 {report}）"
            lines.append(line)
        lines.append("")

    if reported:
        lines.append("**技术说明口径数据：**")
        lines.append("")
        for r in reported:
            val = r["spec_value"]
            unit = r.get("unit")
            item = r["spec_item"]
            line = f"- **{item}**：{val}"
            if unit:
                line += f" {unit}"
            line += "（技术说明口径）"
            lines.append(line)
        lines.append("")

    if not lines:
        # 全部是待补/待确认状态
        lines.append("您查询的参数正在检测或确认中，具体数据陆续上库。")
        lines.append("")
        lines.append("如需获取最新检测进度或技术资料，请留下联系方式，顾问为您对接实验室。")
        return "\n".join(lines)

    if pending:
        lines.append("其余厚度参数检测中，陆续上库。")
        lines.append("")

    return "\n".join(lines)


def answer_pricing(rows: list[dict]) -> str:
    """报价卡：450/400 起 + 报价说明 + 留资引导。"""
    # 找两类的最低起价
    load_price = None
    nonload_price = None
    for r in rows:
        price = r["price_from"]
        ptype = r["panel_type"]
        if ptype == "承重墙板" and (load_price is None or price < load_price):
            load_price = price
        if ptype == "非承重挂板" and (nonload_price is None or price < nonload_price):
            nonload_price = price

    lines = ["**阿格陶粒墙板基础报价（含税出厂）：**", ""]

    if load_price:
        lines.append(f"- 承重墙板（100-200mm）：**{load_price} 元/㎡ 起**")
        lines.append(f"  - 适用：主体承重墙、剪力墙位")
    if nonload_price:
        lines.append(f"- 非承重挂板（100-200mm）：**{nonload_price} 元/㎡ 起**")
        lines.append(f"  - 适用：填充墙、幕墙挂板、隔墙")

    lines.append("")
    lines.append("> 以上为基准出厂价，**最终价格按厚度、构造、订单量与项目地报价**，以正式报价单为准。")
    lines.append("")
    lines.append("📋 **留下联系方式，顾问出具正式报价单与最优方案。**")

    return "\n".join(lines)


def extract_thickness(question: str) -> int:
    """从问题中抓取 100~200 的厚度数字，无则返回 0（全部）。"""
    nums = re.findall(r"\b(1[0-9][0-9]|200)\b", question)
    if nums:
        return int(nums[0])
    return 0


def sse_answer(text: str, source: str, action: str = "answer"):
    """构建 SSE 事件的辅助函数说明 —— 实际 SSE 发送在 main.py 中完成。
    此函数仅返回组装好的元数据，便于 main.py 调用。
    """
    return {"text": text, "source": source, "action": action}