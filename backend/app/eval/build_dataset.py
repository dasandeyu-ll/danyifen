"""生成 200 条中文评测集（确定性，seed 固定，结果落盘可版本管理）。

为什么用"模板 + 随机槽位"而不是手写 200 条？
------------------------------------------
1. **可复现**：seed=20260717，任何人跑都得到同一份数据集，diff 有意义。
2. **可扩展**：想加到 500 条只改计数，不用再写 300 行文本。
3. **标签可信**：文本是"由标签渲染出来的"，所以 ground truth 天然正确，
   不会出现手写数据集时常见的"标注自己标错"问题。
4. **覆盖面可控**：单目标 / 缺槽位 / 多目标 / 模糊表达 四类按比例配比，
   能分别衡量"识别能力"和"该不该转人工"。

数据集构成（共 200 条）
    100 条  单目标完整表达      -> 衡量意图准确率 + 槽位 F1（调优集）
     40 条  一句话两个目标      -> 衡量单句多目标拆分（调优集）
     20 条  模糊/情绪化表达      -> 衡量"该转人工时有没有转"
     10 条  缺必需槽位          -> 衡量"该追问时有没有追问"
     30 条  改写表达（留出集）   -> 衡量泛化能力，**不参与任何调优**

最后这 30 条是这份评测集的价值所在：它让"调优集 1.00 / 留出集 0.83"这个落差
可以被量化出来，而不是拿一个虚高的单一数字自欺欺人。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from ..agent.rules import PRODUCTS

SEED = 20260717

PREFIXES = ["SF", "A", "DD", "JD", "TB", "ZT", "HK", "YT"]
INVOICE_TITLES = [
    "杭州微光科技有限公司",
    "上海云枢信息技术有限公司",
    "北京澜图文化传播有限公司",
    "深圳市恒晟电子有限公司",
]
SPECS = ["黑色旗舰款", "银色28寸", "XL码", "256G版本", "深空灰标准版", "无线充电版"]
CARRIERS = ["中通", "圆通", "顺丰", "韵达", "申通", "极兔"]
AMOUNTS = ["199", "399", "1299", "2699", "58.8", "899"]

# 每个模板的 slots 里允许出现 {order} / {product} / {spec} / {carrier} / {title} 占位符。
# 关键：这些占位符渲染出来的值，必须和 rules.py 里的抽取逻辑保持一致 ——
# 所以 PRODUCTS 直接从 rules.py import，避免"数据集和实现各写一份"的漂移。
TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "退货退款": [
        {"text": "订单号{order}，我买的{product}有质量问题，想申请退货退款。",
         "slots": {"订单号": "{order}", "商品": "{product}", "退款原因": "质量问题"}},
        {"text": "订单{order}的{product}我不想要了，帮我办一下退货。",
         "slots": {"订单号": "{order}", "商品": "{product}", "退款原因": "不喜欢"}},
        {"text": "{product}与描述不符，订单号{order}，我要申请退款。",
         "slots": {"订单号": "{order}", "商品": "{product}", "退款原因": "与描述不符"}},
        {"text": "订单号{order}，{product}收到就破损了，要求退货退款。",
         "slots": {"订单号": "{order}", "商品": "{product}", "退款原因": "质量问题"}},
        {"text": "我要退掉订单{order}里的{product}，走七天无理由退货。",
         "slots": {"订单号": "{order}", "商品": "{product}", "退款原因": "七天无理由"}},
        {"text": "订单{order}，{product}尺码不合适，想退货处理。",
         "slots": {"订单号": "{order}", "商品": "{product}", "退款原因": "尺码不合适"}},
    ],
    "换货": [
        {"text": "订单{order}的{product}发错货了，我想换货，换成{spec}。",
         "slots": {"订单号": "{order}", "商品": "{product}", "换货规格": "{spec}"}},
        {"text": "订单号{order}，{product}有瑕疵，申请换货换成{spec}。",
         "slots": {"订单号": "{order}", "商品": "{product}", "换货规格": "{spec}"}},
        {"text": "我的{product}想换一个，订单{order}，换成{spec}。",
         "slots": {"订单号": "{order}", "商品": "{product}", "换货规格": "{spec}"}},
        {"text": "订单{order}，收到的{product}是坏的，麻烦换新，换成{spec}。",
         "slots": {"订单号": "{order}", "商品": "{product}", "换货规格": "{spec}"}},
    ],
    "投诉": [
        {"text": "我要投诉客服，订单号{order}，一直没人回复我。",
         "slots": {"订单号": "{order}", "投诉对象": "客服"}},
        {"text": "订单{order}，我要投诉商家，说好送的赠品一直没给。",
         "slots": {"订单号": "{order}", "投诉对象": "商家"}},
        {"text": "订单{order}，我要投诉快递公司，东西到现在没送到。",
         "slots": {"订单号": "{order}", "投诉对象": "快递公司"}},
        {"text": "我要举报卖家，订单号{order}，态度极其恶劣。",
         "slots": {"订单号": "{order}", "投诉对象": "卖家"}},
        {"text": "订单{order}，我要投诉客服，说好48小时回电结果没有。",
         "slots": {"订单号": "{order}", "投诉对象": "客服"}},
    ],
    "物流查询": [
        {"text": "帮我查一下订单{order}的物流到哪了，快递是中通。",
         "slots": {"订单号": "{order}", "承运商": "中通"}},
        {"text": "订单{order}的快递三天没更新了，{carrier}到底什么时候派件。",
         "slots": {"订单号": "{order}", "承运商": "{carrier}"}},
        {"text": "{carrier}的单号{order}显示签收但我没收到，帮我查物流。",
         "slots": {"订单号": "{order}", "承运商": "{carrier}"}},
        {"text": "问一下订单{order}的物流进度，{carrier}一直不动。",
         "slots": {"订单号": "{order}", "承运商": "{carrier}"}},
    ],
    "发票": [
        {"text": "订单号{order}，帮我开一张增值税专用发票，抬头：{title}。",
         "slots": {"订单号": "{order}", "发票类型": "增值税专用发票", "抬头": "{title}"}},
        {"text": "订单{order}需要开增值税普通发票，抬头：{title}。",
         "slots": {"订单号": "{order}", "发票类型": "增值税普通发票", "抬头": "{title}"}},
        {"text": "麻烦给订单{order}开票，要增值税专用发票，抬头：{title}。",
         "slots": {"订单号": "{order}", "发票类型": "增值税专用发票", "抬头": "{title}"}},
    ],
    "催单": [
        {"text": "订单{order}还没发货，麻烦今天就安排发出。",
         "slots": {"订单号": "{order}", "期望时效": "今天"}},
        {"text": "订单号{order}下单三天了怎么还不发，麻烦尽快发货。",
         "slots": {"订单号": "{order}", "期望时效": "尽快"}},
        {"text": "订单{order}什么时候发，我等着用，麻烦马上处理。",
         "slots": {"订单号": "{order}", "期望时效": "马上"}},
        {"text": "订单{order}还没发，能不能明天就发货。",
         "slots": {"订单号": "{order}", "期望时效": "明天"}},
    ],
    # 咨询 / 建议没有结构化槽位（主题类槽位是自由文本，不纳入槽位 F1 计算）
    "咨询": [
        {"text": "请问{product}的保修期是多久？", "slots": {}},
        {"text": "想咨询一下{product}该怎么保养？", "slots": {}},
        {"text": "请问{product}支持多少瓦的快充？", "slots": {}},
        {"text": "咨询一下{product}的规格参数是多少？", "slots": {}},
        {"text": "请问有没有{product}的说明书可以下载？", "slots": {}},
    ],
    "建议": [
        {"text": "你们的发货速度很快，包装也很用心，给你们点个赞！", "slots": {}},
        {"text": "建议你们把 APP 的下单流程再简化一点。", "slots": {}},
        {"text": "给客服小姑娘点个赞，态度特别好。", "slots": {}},
        {"text": "提个意见，希望你们能多上一些颜色可以选。", "slots": {}},
        {"text": "整体体验很好，值得推荐给朋友！", "slots": {}},
    ],
}

# 各类目标的数量配比（合计 100）
PER_INTENT = {
    "退货退款": 14,
    "换货": 12,
    "投诉": 13,
    "物流查询": 12,
    "发票": 11,
    "催单": 11,
    "咨询": 13,
    "建议": 14,
}

# --------------------------------------------------------------------------- #
# 留出集（held-out）：故意的"改写样本"
# --------------------------------------------------------------------------- #
# 为什么要单独搞这一组？
# 前面 100+40+10 条是"照着规则词典的表达方式"生成的，规则引擎当然能全对 ——
# 这等价于拿训练集当测试集，属于循环评测，数字再高也没有意义。
# 所以这里额外写 30 条"同一个意思、但换了说法"的中文，
# 其中一部分刻意使用了词典之外的措辞（"寄回去""要回来""适配情况"）。
#
# 结果就是：调优集准确率接近 1.0，留出集大概 0.8 出头。
# 这个**落差本身就是结论** —— 它量化了"关键词匹配的天花板在哪"，
# 也正好论证了为什么要上 DeepSeek 做真正的语义理解。
# 面试被问"你这个 92% 怎么来的"，能说清楚这两个数字的差别，比一个漂亮数字值钱得多。
PARAPHRASE: list[dict[str, Any]] = [
    {"intent": "退货退款", "text": "订单{order}，东西我寄回去，钱麻烦退给我。",
     "slots": {"订单号": "{order}"}},
    {"intent": "退货退款", "text": "订单{order}，这个{product}我不打算要了，你们处理一下吧。",
     "slots": {"订单号": "{order}", "商品": "{product}"}},
    {"intent": "退货退款", "text": "{product}留着没用了，订单号{order}，申请退货。",
     "slots": {"订单号": "{order}", "商品": "{product}"}},
    {"intent": "退货退款", "text": "订单{order}，{product}买重了，走七天无理由退货。",
     "slots": {"订单号": "{order}", "商品": "{product}", "退款原因": "七天无理由"}},
    {"intent": "换货", "text": "收到的{product}不是我要的那款，订单{order}，换成{spec}。",
     "slots": {"订单号": "{order}", "商品": "{product}", "换货规格": "{spec}"}},
    {"intent": "换货", "text": "订单{order}，{product}有点瑕疵，想调个货，换成{spec}。",
     "slots": {"订单号": "{order}", "商品": "{product}", "换货规格": "{spec}"}},
    {"intent": "换货", "text": "订单号{order}，{product}颜色发错了，我要换同款，换成{spec}。",
     "slots": {"订单号": "{order}", "商品": "{product}", "换货规格": "{spec}"}},
    {"intent": "投诉", "text": "订单{order}，这个服务态度我真的要投诉客服。",
     "slots": {"订单号": "{order}", "投诉对象": "客服"}},
    {"intent": "投诉", "text": "订单{order}，他们的处理方式我非常不满，告诉我怎么投诉商家。",
     "slots": {"订单号": "{order}", "投诉对象": "商家"}},
    {"intent": "投诉", "text": "订单{order}，客服太敷衍了，我要举报客服。",
     "slots": {"订单号": "{order}", "投诉对象": "客服"}},
    {"intent": "投诉", "text": "订单{order}，我实在忍不了了，要投诉这个卖家。",
     "slots": {"订单号": "{order}", "投诉对象": "卖家"}},
    {"intent": "物流查询", "text": "订单{order}，我的包裹到底在哪啊，{carrier}一点动静都没有。",
     "slots": {"订单号": "{order}", "承运商": "{carrier}"}},
    {"intent": "物流查询", "text": "订单{order}，麻烦看下{carrier}的运输情况，一直没更新。",
     "slots": {"订单号": "{order}", "承运商": "{carrier}"}},
    {"intent": "物流查询", "text": "订单{order}，显示已签收但我没收到，{carrier}。",
     "slots": {"订单号": "{order}", "承运商": "{carrier}"}},
    {"intent": "物流查询", "text": "订单{order}，{carrier}的派件怎么这么慢。",
     "slots": {"订单号": "{order}", "承运商": "{carrier}"}},
    {"intent": "发票", "text": "订单{order}，这个单子要报销，麻烦给开个票，抬头：{title}。",
     "slots": {"订单号": "{order}", "发票类型": "增值税普通发票", "抬头": "{title}"}},
    {"intent": "发票", "text": "订单{order}，抬头：{title}，麻烦开增值税专用发票。",
     "slots": {"订单号": "{order}", "发票类型": "增值税专用发票", "抬头": "{title}"}},
    {"intent": "发票", "text": "订单{order}，我们公司需要电子发票，抬头：{title}。",
     "slots": {"订单号": "{order}", "发票类型": "增值税普通发票", "抬头": "{title}"}},
    {"intent": "催单", "text": "订单{order}，怎么还没发啊，我这边等着用。",
     "slots": {"订单号": "{order}"}},
    {"intent": "催单", "text": "订单{order}，等了好几天了，麻烦催一下仓库。",
     "slots": {"订单号": "{order}"}},
    {"intent": "催单", "text": "订单{order}，麻烦今天就给我安排发货。",
     "slots": {"订单号": "{order}", "期望时效": "今天"}},
    {"intent": "咨询", "text": "请问订单{order}里{product}的适配情况。", "slots": {}},
    {"intent": "咨询", "text": "{product}的保修政策是怎么样的，帮我说明一下。", "slots": {}},
    {"intent": "咨询", "text": "想了解{product}的尺寸参数是多少。", "slots": {}},
    {"intent": "咨询", "text": "请问{product}有没有现货。", "slots": {}},
    {"intent": "建议", "text": "给你们的服务点个赞，特别满意。", "slots": {}},
    {"intent": "建议", "text": "我觉得你们的界面可以再优化一下，提个意见。", "slots": {}},
    {"intent": "建议", "text": "这次购物体验特别好，值得推荐给我同事。", "slots": {}},
    {"intent": "建议", "text": "希望你们能多搞一些活动，回馈老客户。", "slots": {}},
    {"intent": "建议", "text": "你们这个功能做得很好，表扬一下产品团队。", "slots": {}},
]

# 模糊 / 情绪化表达：没有明确意图，正确行为是"转人工"而不是硬猜
HARD_TEXTS = [
    "那个东西怎么又出问题了，我说不清楚，反正就是很烦。",
    "你们自己看着办吧，我不想再说了。",
    "算了，随便吧，我也不知道该怎么办了。",
    "唉，真是服了，还能说什么呢。",
    "这个情况你们应该比我清楚，我就不多说了。",
    "无语了，真的无语了。",
    "上次那个事到现在也没解决，我很失望。",
    "东西收到了，就那样吧。",
    "你们看着处理就行，我没什么意见。",
    "本来挺期待的，现在心情很复杂。",
    "我也不知道该找谁了，你们内部消化一下吧。",
    "随便问问，没什么特别的事。",
    "反正就是各种不顺，一言难尽。",
    "算了不说了，浪费我时间。",
    "唉，心累，就这样吧。",
    "呵呵，好得很。",
    "我还能说什么，就这样吧。",
    "麻烦帮我转下人工，谢谢。",
    "事情有点复杂，我不知道从哪说起。",
    "东西是好东西，就是有点小插曲。",
]

# 缺必需槽位：用户说了意图但没给关键信息，正确行为是"追问"
MISSING_TEMPLATES = [
    {"intent": "退货退款", "text": "我买的{product}有质量问题，想申请退货退款。", "slots": {"商品": "{product}", "退款原因": "质量问题"}},
    {"intent": "换货", "text": "我的{product}想换货，麻烦帮我处理一下。", "slots": {"商品": "{product}"}},
    {"intent": "发票", "text": "帮我开一张增值税专用发票，抬头：{title}。", "slots": {"发票类型": "增值税专用发票", "抬头": "{title}"}},
    {"intent": "催单", "text": "我的订单怎么还没发货，麻烦尽快处理。", "slots": {"期望时效": "尽快"}},
    {"intent": "物流查询", "text": "我的快递三天没更新了，帮我查一下物流进度。", "slots": {}},
    {"intent": "投诉", "text": "我要投诉，太离谱了，完全没人管。", "slots": {}},
    {"intent": "退货退款", "text": "{product}收到就破损了，要求退货退款。", "slots": {"商品": "{product}", "退款原因": "质量问题"}},
    {"intent": "换货", "text": "发错货了，我想申请换货处理。", "slots": {}},
    {"intent": "催单", "text": "订单还没发货吗，我等着用呢。", "slots": {}},
    {"intent": "发票", "text": "麻烦帮我开个票，要报销用的。", "slots": {}},
]

# 多目标组合时用两个不同意图拼接，保证拆分结果唯一
MULTI_PAIRS = [
    ("退货退款", "咨询"),
    ("退货退款", "投诉"),
    ("换货", "物流查询"),
    ("催单", "发票"),
    ("投诉", "物流查询"),
    ("退货退款", "催单"),
    ("换货", "咨询"),
    ("投诉", "催单"),
    ("物流查询", "建议"),
    ("发票", "咨询"),
]


def make_order(rng: random.Random) -> str:
    prefix = rng.choice(PREFIXES)
    digits = "".join(rng.choice("0123456789") for _ in range(rng.randint(8, 12)))
    return f"{prefix}{digits}"


def render(template: dict[str, Any], rng: random.Random) -> tuple[str, dict[str, str], dict[str, str]]:
    """返回 (渲染后的文本, 期望槽位, 渲染时实际用到的占位符值)。"""
    values = {
        "order": make_order(rng),
        "product": rng.choice(PRODUCTS),
        "spec": rng.choice(SPECS),
        "carrier": rng.choice(CARRIERS),
        "title": rng.choice(INVOICE_TITLES),
        "amount": rng.choice(AMOUNTS),
    }
    text = template["text"].format(**values)
    slots = {k: v.format(**values) for k, v in template.get("slots", {}).items()}
    return text, slots, values


def build() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    records: list[dict[str, Any]] = []

    def add(text: str, goals: list[dict[str, Any]], tag: str) -> None:
        records.append(
            {
                "id": f"ev_{len(records) + 1:04d}",
                "text": text,
                "goals": goals,
                "tag": tag,
            }
        )

    # ---- 1. 单目标完整表达 ------------------------------------------------ #
    for intent, count in PER_INTENT.items():
        pool = TEMPLATES[intent]
        for i in range(count):
            template = pool[i % len(pool)]
            text, slots, _ = render(template, rng)
            add(text, [{"intent": intent, "slots": slots}], "single")

    # ---- 2. 单句多目标 --------------------------------------------------- #
    # 组合时保证两条子句的意图不同，且各自模板里都能被独立识别出来
    for i in range(40):
        first_intent, second_intent = MULTI_PAIRS[i % len(MULTI_PAIRS)]
        t1 = TEMPLATES[first_intent][i % len(TEMPLATES[first_intent])]
        t2 = TEMPLATES[second_intent][(i * 3 + 1) % len(TEMPLATES[second_intent])]
        text1, slots1, _ = render(t1, rng)
        text2, slots2, _ = render(t2, rng)
        joiner = "。另外，" if i % 2 == 0 else "。还有，"
        # 去掉第一条的子句结尾标点，保持一段连贯的中文
        text = text1.rstrip("。！") + joiner + text2
        add(
            text,
            [
                {"intent": first_intent, "slots": slots1},
                {"intent": second_intent, "slots": slots2},
            ],
            "multi",
        )

    # ---- 3. 模糊表达（期望转人工） ---------------------------------------- #
    for text in HARD_TEXTS:
        add(text, [], "hard")

    # ---- 4. 缺必需槽位（期望追问） ---------------------------------------- #
    for template in MISSING_TEMPLATES:
        text, slots, _ = render(template, rng)
        add(text, [{"intent": template["intent"], "slots": slots}], "missing_slot")

    # ---- 5. 留出集：改写样本（衡量泛化，不是衡量调优） -------------------- #
    for item in PARAPHRASE:
        text, slots, _ = render(item, rng)
        add(text, [{"intent": item["intent"], "slots": slots}], "paraphrase")

    return records


def main() -> None:
    records = build()
    out = Path(__file__).with_name("dataset.jsonl")
    with out.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats: dict[str, int] = {}
    for r in records:
        stats[r["tag"]] = stats.get(r["tag"], 0) + 1
    print(f"已生成 {len(records)} 条评测样本 -> {out}")
    for tag, n in stats.items():
        print(f"  {tag:14s} {n:>3d} 条")


if __name__ == "__main__":
    main()
