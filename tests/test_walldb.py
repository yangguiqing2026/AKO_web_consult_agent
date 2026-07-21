"""AKO Phase 3 (DB-002) WallDB 接入验收 —— 8 项断言"""
import json
import os
import sys

import requests

BASE = "http://localhost:7863"

PASS_COUNT = 0
FAIL_COUNT = 0


def test(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"[PASS] {name}")
    else:
        FAIL_COUNT += 1
        print(f"[FAIL] {name}  --  {detail}")


# 1. 「你们墙板多厚」→ 直答六档厚度，action=answer，source=《阿格墙板数据库》
print("=== Test 1: 规格型号直答 (struct_panels) ===")
try:
    r = requests.post(
        f"{BASE}/api/chat",
        json={"question": "你们墙板多厚"},
        timeout=30,
    )
    data = r.json()
    action = data.get("action", "")
    answer = data.get("answer", "")
    sources = data.get("sources", [])
    source_names = [s.get("display_name", "") for s in sources]

    test("1.1 action=answer", action == "answer", f"action={action}")
    test("1.2 含六档厚度标记", "100mm" in answer and "200mm" in answer,
         f"answer preview: {answer[:200]}")
    test("1.3 source=《阿格墙板数据库》", "《阿格墙板数据库》" in source_names,
         f"sources={source_names}")
    test("1.4 含承重墙板/非承重挂板分类",
         "承重墙板" in answer and "非承重挂板" in answer,
         f"answer preview: {answer[:200]}")
except Exception as e:
    test("1.x 规格型号接口异常", False, str(e))

# 2. 「120 的隔声量多少」→ 回答含 46(-1;-4)dB 与 BETC-JN1-2018-00206
print("\n=== Test 2: 性能参数直答 (struct_specs) ===")
try:
    r = requests.post(
        f"{BASE}/api/chat",
        json={"question": "120的隔声量多少"},
        timeout=30,
    )
    data = r.json()
    answer = data.get("answer", "")
    action = data.get("action", "")

    test("2.1 action=answer", action == "answer", f"action={action}")
    test("2.2 含隔声值 46(-1;-4)dB", "46(-1;-4)" in answer,
         f"answer preview: {answer[:300]}")
    test("2.3 含报告编号 BETC-JN1-2018-00206",
         "BETC-JN1-2018-00206" in answer,
         f"answer preview: {answer[:300]}")
except Exception as e:
    test("2.x 性能参数异常", False, str(e))

# 3. 「多少钱一平」→ 报价卡含 450/400 起 + 报价说明 + 留资引导
print("\n=== Test 3: 报价卡 (struct_pricing) ===")
try:
    r = requests.post(
        f"{BASE}/api/chat",
        json={"question": "多少钱一平"},
        timeout=30,
    )
    data = r.json()
    answer = data.get("answer", "")
    action = data.get("action", "")

    test("3.1 action=answer（非 lead）", action == "answer", f"action={action}")
    test("3.2 含 450 或 400 起", ("450" in answer) or ("400" in answer),
         f"answer preview: {answer[:300]}")
    test("3.3 含报价说明「起」字", "起" in answer,
         f"answer preview: {answer[:300]}")
    test("3.4 含留资引导",
         "留下联系方式" in answer or "报价单" in answer,
         f"answer preview: {answer[:300]}")
except Exception as e:
    test("3.x 报价卡异常", False, str(e))

# 4. 「140mm 的检测报告」→ 回答注明检测中 + 留资引导，无编造数值（待补状态）
#    注：specs 全部为实测，无待补行时走实测输出。本题验证不含待补行时正确触达。
#    如 specs 中有 140mm 专属数据则验证含报告编号
print("\n=== Test 4: 检测报告 (struct_specs) ===")
try:
    r = requests.post(
        f"{BASE}/api/chat",
        json={"question": "140mm的检测报告"},
        timeout=30,
    )
    data = r.json()
    answer = data.get("answer", "")
    action = data.get("action", "")

    test("4.1 action=answer", action == "answer", f"action={action}")
    test("4.2 含报告编号或检测相关信息",
         "BETC" in answer or "实测" in answer or "检测" in answer,
         f"answer preview: {answer[:300]}")
except Exception as e:
    test("4.x 检测报告异常", False, str(e))

# 5. Health 端点含 wall_api 状态
print("\n=== Test 5: /health 含 wall_api 字段 ===")
try:
    r = requests.get(f"{BASE}/health", timeout=10)
    data = r.json()
    wall_status = data.get("wall_api", "missing")
    test("5.1 wall_api 字段存在", wall_status != "missing", f"wall_api={wall_status}")
    test("5.2 wall_api 值为 up/down/disabled",
         wall_status in ("up", "down", "disabled"),
         f"wall_api={wall_status}")
except Exception as e:
    test("5.x health 异常", False, str(e))

# 6. 成本红线：问「成本多少」→ 不应暴露成本数据
print("\n=== Test 6: 成本红线 ===")
try:
    # "成本"关键词在 _LEAD_KEYWORDS 中，会走 lead
    r = requests.post(
        f"{BASE}/api/chat",
        json={"question": "墙板成本多少"},
        timeout=30,
    )
    data = r.json()
    answer = data.get("answer", "")

    test("6.1 不出现成本数字", "450" not in answer.lower() and "400" not in answer.lower(),
         f"answer preview: {answer[:200]}")
except Exception as e:
    test("6.x 成本红线异常", False, str(e))

# 7. 关键回归：FAQ 类问题仍走检索
print("\n=== Test 7: FAQ 回归（非 wall 关键词） ===")
try:
    r = requests.post(
        f"{BASE}/api/chat",
        json={"question": "陶粒墙板有什么优点"},
        stream=True,
        timeout=30,
    )
    ctype = r.headers.get("content-type", "")
    test("7.1 SSE 流式响应", "text/event-stream" in ctype, f"got: {ctype}")
except Exception as e:
    test("7.x FAQ 回归异常", False, str(e))

# 8. 关键词争夺：规格+价格 → 报价卡优先
print("\n=== Test 8: 关键词优先级 (规格+价格 → struct_pricing) ===")
try:
    r = requests.post(
        f"{BASE}/api/chat",
        json={"question": "120mm 墙板价格"},
        timeout=30,
    )
    data = r.json()
    action = data.get("action", "")
    answer = data.get("answer", "")
    test("8.1 action=answer（struct_pricing 优先）", action == "answer",
         f"action={action}")
    test("8.2 报价内容优先于规格内容",
         "报价" in answer or "起" in answer or "价格" in answer,
         f"answer preview: {answer[:200]}")
except Exception as e:
    test("8.x 优先级异常", False, str(e))


print(f"\n{'='*40}")
print(f"WallDB 验收结果: {PASS_COUNT}/{PASS_COUNT + FAIL_COUNT} PASS")
if FAIL_COUNT > 0:
    print(f"{FAIL_COUNT} 项失败，请检查失败项！")
    sys.exit(1)
else:
    print("WallDB 接入验收全过！")
    sys.exit(0)