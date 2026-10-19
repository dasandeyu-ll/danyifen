"""离线规则分析器（Rule Analyzer）—— 零依赖、零延迟、可解释的兜底引擎。

为什么要有它？
--------------
1. 工程上的"降级路径"：大模型超时/限流/没配 key 时，系统必须还能用。
   生产里这叫 graceful degradation，面试时是个很好的加分点。
2. 评测基准：要证明"大模型比基线好"，你得先有一个基线。
3. 单元测试友好：确定性输出，CI 里跑得快。

它本身也是认真的实现，不是糊弄的占位符：带权关键词词典 + 正则槽位抽取
+ 证据量校准的置信度。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..schemas import Intent, SLOT_SCHEMA

# --------------------------------------------------------------------------- #
# 1. 意图词典（带权重：越有辨识力的词权重越高）
# --------------------------------------------------------------------------- #
INTENT_LEXICON: dict[Intent, dict[str, float]] = {
    Intent.RETURN: {
        "退货": 3, "退款": 3, "退钱": 3, "申请退": 3, "不想要了": 3, "七天无理由": 3,
        "退回": 2, "退掉": 3, "退一下": 3, "想退": 3, "退一赔三": 2, "返款": 2, "原路退回": 3,
    },
    Intent.EXCHANGE: {
        "换货": 3, "换新": 3, "换成": 2, "换个": 2, "换一个": 3, "调换": 3,
        "换一换": 3, "补发一个": 2, "重新发一个": 2, "换同款": 3,
    },
    Intent.COMPLAINT: {
        "投诉": 3, "举报": 3, "差评": 2, "态度恶劣": 3, "客服态度": 3, "欺骗": 2,
        "违约": 2, "严重不满": 2, "垃圾服务": 3, "太差了": 2, "发火": 2, "维权": 2, "曝光": 2,
    },
    Intent.LOGISTICS: {
        "物流": 3, "运单": 3, "快递": 2, "到哪": 2, "签收": 2, "派件": 2, "没收到": 3,
        "查快递": 3, "单号查": 3, "一直没动": 2, "停滞": 2, "运输": 2, "配送": 2,
    },
    Intent.INVOICE: {
        "发票": 3, "开票": 3, "专票": 3, "普票": 3, "税号": 3, "抬头": 2,
        "增值税": 3, "电子发票": 3, "报销": 2, "开个票": 3,
    },
    Intent.URGE: {
        "催": 3, "催单": 3, "什么时候发": 3, "还没发货": 3, "还没发": 3, "怎么还不发": 3,
        "加急": 2, "尽快发货": 3, "多久能到": 2, "急着用": 3, "马上要": 2, "发了吗": 3,
    },
    Intent.CONSULT: {
        "咨询": 3, "请问": 1.5, "怎么用": 2, "如何使用": 2, "能不能": 1.5, "是否支持": 2,
        "保修": 2, "保质期": 2, "多少钱": 2, "有没有货": 2, "规格": 1.5, "区别": 1.5,
        "说明书": 2, "兼容": 1.5, "怎么操作": 2,
    },
    Intent.SUGGESTION: {
        "建议": 3, "表扬": 3, "点赞": 3, "点个赞": 3, "感谢": 2, "希望你们": 2,
        "提个意见": 3, "反馈一下": 2, "好评": 2, "服务很好": 2, "值得推荐": 3,
    },
}

# 转折/并列词：用来做"单句多目标拆分"
CONJUNCTIONS = [
    "另外", "还有", "同时", "并且", "而且", "以及", "此外", "另外一个",
    "顺带", "顺便", "再顺便", "再加上", "然后还", "另一个问题",
]

# 明确的"多目标信号词"，出现即认为这句里藏了不止一个目标
MULTI_GOAL_MARKERS = CONJUNCTIONS + ["；", ";", "、", "一是", "二是"]

PRODUCTS = [
    "显示器", "笔记本电脑", "笔记本", "平板电脑", "平板", "手机壳", "手机", "蓝牙耳机",
    "耳机", "键盘", "鼠标", "机械键盘", "打印机", "扫地机器人", "扫地机", "空调",
    "咖啡机", "行李箱", "面膜", "相机", "充电宝", "运动鞋", "跑鞋", "连衣裙",
    "保温杯", "水杯", "羽绒服", "手表", "智能手表", "路由器", "投影仪", "加湿器",
    "电动牙刷", "电饭煲", "空气炸锅", "吹风机", "床垫", "沙发", "台灯", "背包",
]

CARRIERS = ["顺丰", "中通", "圆通", "申通", "韵达", "京东物流", "京东快递", "EMS", "ems", "极兔", "德邦", "邮政"]

REFUND_REASONS = {
    "质量问题": ["质量问题", "有质量", "坏了", "损坏", "故障", "不能用了", "漏电", "破损", "破了个"],
    "发错货": ["发错", "发错货", "不是我要的", "拍错了发错", "货不对板"],
    "不喜欢": ["不喜欢", "不想要", "款式不好看", "颜色不好看", "冲动消费"],
    "与描述不符": ["与描述不符", "和描述不一样", "货不对版", "图文不符", "跟图片不一样"],
    "尺码不合适": ["尺码不", "太大", "太小", "穿着紧", "偏小", "偏大"],
    "七天无理由": ["七天无理由", "7天无理由", "无理由退"],
    "包装破损": ["包装破损", "外包装破", "运输破损"],
}

# --------------------------------------------------------------------------- #
# 2. 正则
# --------------------------------------------------------------------------- #
PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
ORDER_LABELED = re.compile(
    r"(?:订单号|订单编号|单号|运单号?|order\s*(?:no|id)?)\s*[：:是为]?\s*([A-Za-z0-9][A-Za-z0-9\-]{5,23})",
    re.I,
)
ORDER_LOOSE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,4}\d{8,16}|\d{12,20})(?![A-Za-z0-9])")
AMOUNT = re.compile(r"(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块)")
DATE_RES = [
    re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]"),
    re.compile(r"(?:今天|昨天|前天|大前天|上周[一二三四五六日天]?|上个月|本周|这周|周[一二三四五六日天])"),
]
URGENCY = re.compile(r"(今天|明天|后天|尽快|马上|立刻|48\s*小时|24\s*小时|本周内|这周|周末前)")

# "换成 XXX"：负向先行断言挡掉"换货 / 换新 / 换一个"这类不含规格的搭配，
# 否则"我想换货，换成黑色款"会被错误地抽成 spec="货"。
SPEC_HINT = re.compile(
    r"(?:换成|换为|改成为|改换为|换)\s*(?![货新一同样种个])([A-Za-z0-9\u4e00-\u9fa5\-]{1,14}?)(?:的|，|。|,|$|；|;|！|!)"
)
SIZE = re.compile(r"((?:[2-5]\d|XXS|XS|S|M|L|XL|XXL|XXXL|均码)\s*码)(?![A-Za-z0-9])", re.I)
COMPLAINT_TARGET = re.compile(
    r"(?:投诉|举报|曝光|差评)\s*(?:一下)?\s*([^\s，。！？,;；、]{2,10})"
)
PRODUCT_AFTER_VERB = re.compile(r"(?:买的|买了|购买的|下单的|订购的|买了个|买的那个)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,12})")

# "这个 / 那个 / 该" 这类指示代词在槽位值里没有信息量，统一剥掉
DETERMINERS = ("这个", "那个", "该", "本", "我们", "你们")


@dataclass
class RuleHit:
    intent: Intent
    raw: float
    prob: float
    matched: list[str]


def normalize(text: str) -> str:
    """全角转半角 + 压缩空白，让正则不必处理两套字符集。"""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# 3. 意图分类（多标签：给出所有候选意图的得分分布）
# --------------------------------------------------------------------------- #
def raw_scores(text: str) -> dict[Intent, tuple[float, list[str]]]:
    scores: dict[Intent, tuple[float, list[str]]] = {}
    for intent, lexicon in INTENT_LEXICON.items():
        total = 0.0
        hits: list[str] = []
        for word, weight in lexicon.items():
            n = text.count(word)
            if n:
                # 同一关键词重复出现收益递减，避免刷词
                total += weight * (1 + 0.25 * (n - 1))
                hits.append(word)
        if total:
            scores[intent] = (total, hits)
    return scores


def classify(text: str) -> tuple[list[RuleHit], float]:
    """返回 (排序后的候选列表, 置信度)。

    置信度不是拍脑袋的 0.9，而是两路证据校准出来的：
      * top_prob  : 归一化后的"相对优势" —— 说明模型有多确信这是第一意图
      * top_raw   : 绝对证据量 —— 说明这句话本身携带了多少可判别信息
    两者都高才给高置信度。只有 top_prob 高而 raw 很低（比如只命中一个 1.5 分的
    "请问"），说明是弱信号，应该转人工。
    """
    text = normalize(text)
    scores = raw_scores(text)
    if not scores:
        return [RuleHit(Intent.OTHER, 0.0, 1.0, [])], 0.30

    total = sum(v[0] for v in scores.values())
    hits = [
        RuleHit(intent, raw, raw / total, matched)
        for intent, (raw, matched) in scores.items()
    ]
    hits.sort(key=lambda h: h.raw, reverse=True)

    top = hits[0]
    second_raw = hits[1].raw if len(hits) > 1 else 0.0
    margin = (top.raw - second_raw) / top.raw if top.raw else 0.0

    conf = 0.30 + 0.34 * top.prob + 0.22 * min(1.0, top.raw / 5.0) + 0.16 * margin
    return hits, round(min(0.97, conf), 3)


# --------------------------------------------------------------------------- #
# 4. 单句多目标拆分
# --------------------------------------------------------------------------- #
def split_goals(text: str) -> list[str]:
    """把一条留言拆成若干"目标句子"。

    策略（故意的两段式，比一刀切 split 稳得多）：
      1) 先粗切：按并列连词 / 分号 / 句号切开；
      2) 再回并：如果某个碎片自己"没有意图证据"，说明它是上一句的补充信息
         （比如"另外，订单号是 12345"），必须并回去，否则会造出一个假目标。
    第 2 步是关键 —— 简单 split 的项目就死在这里。
    """
    text = normalize(text)
    if not text:
        return []

    pattern = "|".join(re.escape(c) for c in CONJUNCTIONS)
    rough = re.split(rf"(?:{pattern})|[；;。！？!?]+|\n", text)
    rough = [s.strip(" ，,、") for s in rough if s and s.strip(" ，,、")]

    segments: list[str] = []
    for piece in rough:
        has_evidence = bool(raw_scores(piece))
        if segments and not has_evidence:
            segments[-1] = f"{segments[-1]}，{piece}"
        else:
            segments.append(piece)

    if not segments:
        segments = [text]

    # 兜底：一整句里两个意图的得分都很高（说明是靠"、"连接的多目标），
    # 就按关键词边界再切一次
    if len(segments) == 1:
        seg = segments[0]
        scores = raw_scores(seg)
        strong = [i for i, (raw, _) in scores.items() if raw >= 3]
        if len(strong) >= 2 and len(seg) > 18:
            sub = re.split(r"、|而且|以及", seg)
            sub = [s.strip(" ，,、") for s in sub if s.strip(" ，,、")]
            if len(sub) > 1 and all(raw_scores(s) for s in sub):
                segments = sub

    return segments[:5]  # 一条留言超过 5 个目标属于异常输入，截断防止刷爆下游


# --------------------------------------------------------------------------- #
# 5. 槽位抽取
# --------------------------------------------------------------------------- #
def _first(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return None


def extract_slots(text: str, intent: Intent) -> dict[str, str]:
    text = normalize(text)
    wanted = set(SLOT_SCHEMA.get(intent, []))
    found: dict[str, str] = {}

    # 手机号先摘掉，否则 11 位数字会被误认成订单号
    masked = PHONE.sub(" ", text)

    if "订单号" in wanted:
        order = _first([ORDER_LABELED, ORDER_LOOSE], masked)
        if order:
            found["订单号"] = order.upper()

    if "商品" in wanted:
        hit = next((p for p in PRODUCTS if p in text), None)
        if not hit:
            m = PRODUCT_AFTER_VERB.search(text)
            hit = m.group(1) if m else None
        if hit:
            found["商品"] = hit

    if "退款原因" in wanted:
        for reason, kws in REFUND_REASONS.items():
            if any(k in text for k in kws):
                found["退款原因"] = reason
                break

    if "换货规格" in wanted:
        spec = None
        m = SPEC_HINT.search(text)
        if m:
            spec = m.group(1).strip()
        if not spec:
            m = SIZE.search(text)
            if m:
                spec = m.group(1).replace(" ", "")
        if not spec:
            for p in PRODUCTS:
                if f"换成{p}" in text or f"换{p}" in text:
                    spec = p
                    break
        if spec:
            found["换货规格"] = spec

    if "投诉对象" in wanted:
        m = COMPLAINT_TARGET.search(text)
        target = m.group(1).strip() if m else None
        if target:
            for det in DETERMINERS:
                if target.startswith(det) and len(target) > len(det):
                    target = target[len(det) :]
                    break
        if not target:
            for kw in ["客服", "商家", "卖家", "物流", "快递", "平台", "店家", "售后"]:
                if kw in text:
                    target = kw
                    break
        if target:
            found["投诉对象"] = target

    if "投诉事由" in wanted:
        m = re.search(r"(?:因为|由于|说好|承诺)([^，。；]{2,24})", text)
        if m:
            found["投诉事由"] = m.group(1).strip()
        elif "态度" in text:
            found["投诉事由"] = "服务态度问题"
        elif "延迟" in text or "慢" in text:
            found["投诉事由"] = "处理时效问题"

    if "承运商" in wanted:
        hit = next((c for c in CARRIERS if c in text), None)
        if hit:
            found["承运商"] = hit.upper() if hit.islower() else hit

    if "发票类型" in wanted:
        if any(k in text for k in ["专票", "增值税专用"]):
            found["发票类型"] = "增值税专用发票"
        elif any(k in text for k in ["普票", "普通发票", "电子发票", "开个票", "发票"]):
            found["发票类型"] = "增值税普通发票"

    if "抬头" in wanted:
        m = re.search(r"抬头\s*[：:是为]?\s*([\u4e00-\u9fa5A-Za-z0-9（）()]{2,30})", text)
        if m:
            found["抬头"] = m.group(1).strip()

    if "期望时效" in wanted:
        m = URGENCY.search(text)
        if m:
            found["期望时效"] = m.group(1)

    if "咨询主题" in wanted:
        for p in PRODUCTS:
            if p in text:
                found["咨询主题"] = f"{p}相关"
                break
        else:
            m = re.search(r"(?:咨询|问一下|请问)(?:一下)?([^，。；？?]{2,20})", text)
            if m:
                found["咨询主题"] = m.group(1).strip()

    if "建议主题" in wanted:
        m = re.search(r"(?:建议|意见|反馈)(?:一下)?([^，。；]{2,20})", text)
        found["建议主题"] = m.group(1).strip() if m else "服务体验"

    # 通用槽位：日期 / 金额 作为附加信息挂到日期相关槽位
    if "日期" in wanted:
        d = _first(DATE_RES, text)
        if d:
            found["日期"] = d

    return found


def intent_needs_human_review(hits: list[RuleHit], confidence: float, threshold: float, margin: float) -> tuple[bool, str]:
    if not hits or hits[0].intent is Intent.OTHER:
        return True, "未能识别出明确意图"
    if confidence < threshold:
        return True, f"置信度 {confidence:.2f} 低于阈值 {threshold:.2f}"
    if len(hits) > 1 and (hits[0].raw - hits[1].raw) / max(hits[0].raw, 1e-6) < margin:
        return True, f"「{hits[0].intent.value}」与「{hits[1].intent.value}」难以区分"
    return False, ""
