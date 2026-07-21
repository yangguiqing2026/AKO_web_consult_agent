"""AKO 网站咨询网关 - 意图路由（关键词规则，优先级排序）— DB-002 增强"""

from typing import Literal

# WallDB 结构化直答规则（DB-002 §1.1：优先级高于 R001 商机留资）
_STRUCT_RULES = [
    # R030 报价卡 - priority 10（最高优先级，避免被 R001 拦截）
    (["价格", "报价", "多少钱", "造价", "单价"], "struct_pricing"),
    # R020 性能参数直答 - priority 9
    (["隔声", "隔音", "耐火", "防火", "抗风", "水密", "气密", "放射性", "环保",
      "吊挂", "软化", "防潮", "保温", "传热", "检测报告"], "struct_specs"),
    # R010 规格型号直答 - priority 8
    (["厚度", "规格", "型号", "多厚", "板幅", "尺寸", "多大"], "struct_panels"),
]

# 商机/留资 — 中优先级（匹配即返回，不继续检查）
_LEAD_KEYWORDS_PRIORITY = [
    # 商务合作
    ["合作", "代理", "加盟", "经销商", "渠道", "批发", "采购", "订单", "供货"],
    # 留联系方式
    ["电话", "微信", "咨询", "留资", "预约", "参观", "样板房", "考察", "面谈", "联系"],
    # 询盘意向
    ["怎么买", "如何购买", "下单", "询价", "询盘", "定制"],
    # 费用/预算类（非明确报价仍走留资）
    ["费用", "成本", "预算", "总价", "收费"],
]

# 闲聊收敛
_CHITCHAT_KEYWORDS = [
    "你好", "在吗", "谢谢", "再见", "拜拜", "嗨", "哈喽",
    "你是谁", "你是", "什么助手", "机器人",
]

# FAQ 行业域关键词（必须有这些词才走 FAQ，否则降级闲聊）
_DOMAIN_KEYWORDS = [
    "墙板", "陶粒", "装配式", "模块", "箱体", "建筑", "施工",
    "隔声", "隔音", "耐火", "防火", "防水", "防潮", "抗震",
    "保温", "强度", "承重", "安装", "标准", "系数", "参数", "性能",
    "板材", "混凝土", "发泡", "一体化", "预制", "构件",
    "隔墙", "外墙", "内墙", "楼板", "屋面", "基础",
    "T/CECS", "检测", "规范", "验收", "节点", "工艺",
    "自建房", "别墅", "民宿", "厂房", "住宅", "公建",
    "多重", "重量", "容重", "每平米", "平米", "平方", "面积",
    "尺寸", "规格", "厚度", "材料", "结构",
]


def route(question: str) -> Literal["struct_panels", "struct_specs", "struct_pricing", "lead", "chitchat", "faq"]:
    """优先级路由：struct_* > lead > chitchat > 行业词命中 faq > 降级 chitchat"""
    q = question.strip()
    q_lower = q.lower()

    # 1) WallDB 结构化规则 — 最高优先级，匹配即返回，不继续检查
    for keywords, action in _STRUCT_RULES:
        for kw in keywords:
            if kw in q_lower:
                return action  # type: ignore[return-value]

    # 2) Lead 商机留资：按组匹配，任一组命中即返回
    for group in _LEAD_KEYWORDS_PRIORITY:
        for kw in group:
            if kw in q_lower:
                return "lead"

    # 3) Chitchat
    for kw in _CHITCHAT_KEYWORDS:
        if kw in q_lower:
            return "chitchat"

    # 4) 过短输入 → chitchat
    if len(q) < 4:
        return "chitchat"

    # 5) 行业域检查：必须至少命中一个行业词才走 FAQ
    if any(kw in q_lower for kw in _DOMAIN_KEYWORDS):
        return "faq"

    # 6) 其他 → chitchat 兜底
    return "chitchat"