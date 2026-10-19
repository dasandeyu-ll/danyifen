"""Prompt 集中管理。

为什么单独抽一个文件？
1. Prompt 是"代码里的配置"，散落在业务逻辑里就没人敢改、也没法做 A/B；
2. 两阶段（分类 / 抽槽）各自有独立的系统提示，分开维护边界更清楚；
3. 评测时可以把 prompt 版本号带进结果里，方便对比迭代。
"""

from __future__ import annotations

from ..schemas import Intent, REQUIRED_SLOTS, SLOT_SCHEMA

PROMPT_VERSION = "v1.2"

INTENT_GUIDE = "\n".join(
    f"- {i.value}：{desc}"
    for i, desc in [
        (Intent.RETURN, "用户想退货、退款、退钱、取消不想买了"),
        (Intent.EXCHANGE, "用户想换货、换型号、换尺码、补发"),
        (Intent.COMPLAINT, "用户表达不满、投诉、举报、要求追责"),
        (Intent.LOGISTICS, "用户想查物流进度、快递到哪了、是否签收"),
        (Intent.INVOICE, "用户要开发票、问发票、要改抬头税号"),
        (Intent.URGE, "用户催促发货、催处理、要求加急"),
        (Intent.CONSULT, "用户询问商品/政策/使用方法等中性问题"),
        (Intent.SUGGESTION, "用户提建议、表扬、给好评"),
        (Intent.OTHER, "以上都不属于，或信息不足以判断"),
    ]
)

CLASSIFY_SYSTEM = f"""你是电商客服工单的意图识别引擎。你要对用户留言拆出的每一个"目标句子片段"做**多标签意图分类**。

可选意图（必须严格使用这些名字）：
{INTENT_GUIDE}

分类规则：
1. 一个片段可以有多个意图，按分数从高到低排列，分数是 0~1 的浮点数。
2. 首要意图的分数代表你的确信程度。请不要一律给 0.9，要诚实反映难度：
   - 用户明确说了"我要退货"→ 0.9 以上
   - 只是抱怨"这东西怎么这么难用"→ 0.5 左右，且可能命中 咨询/投诉
   - 完全看不出意图 → 只给 其他 1.0
3. 所有分数都要真的体现相对可能性，会用于决定是否转人工，所以请谨慎。

只输出 JSON，不要解释、不要 markdown 代码块。JSON 结构：
{{"results": [{{"index": 0, "intents": [{{"name": "退货退款", "score": 0.92}}, {{"name": "投诉", "score": 0.31}}]}}]}}"""


def build_extract_system() -> str:
    slot_lines = []
    for intent, slots in SLOT_SCHEMA.items():
        if not slots:
            continue
        required = set(REQUIRED_SLOTS.get(intent, []))
        marked = "、".join(f"{s}{'（必需）' if s in required else ''}" for s in slots)
        slot_lines.append(f"- {intent.value}：{marked}")
    slot_table = "\n".join(slot_lines)

    return f"""你是电商客服工单的槽位抽取引擎。你要从用户的话里抽取结构化槽位。

各意图需要抽取的槽位（标了"必需"的槽位抽不到就会触发追问）：
{slot_table}

抽取规则：
1. **只抽用户真的说了的内容**。不要臆造、不要脑补常见的订单号。
2. 订单号/快递单号要原样保留大小写和连字符；金额只留数字；日期保留用户原话。
3. 商品名抽取品类即可（如"显示器""运动鞋"），不要带修饰语。
4. 抽不到的槽位直接不要出现这个 key，绝对不要填 null 或空字符串。
5. 每个槽位给出 confidence（0~1）和 source：
   - text：用户原话里明确写了
   - inferred：根据上下文合理推断（例如从"上周下的单"推断出日期）

只输出 JSON，不要解释、不要 markdown 代码块。JSON 结构：
{{"intent": "退货退款", "slots": {{"订单号": {{"value": "SF1234567890", "confidence": 0.95, "source": "text"}}}}}}"""
