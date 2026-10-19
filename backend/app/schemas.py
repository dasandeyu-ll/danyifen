"""领域模型：用 Pydantic 描述一条工单里"目标(Goal)"的完整生命周期。

核心思想
--------
客服留言不是"一条 = 一个意图"，而是"一条留言里可能藏着多个目标"。
例如："我要退掉那台显示器，另外投诉一下上个客服的态度" —— 这是 2 个目标。

所以建模成  Ticket (1) ──< Goal (N) ，每个 Goal 独立走状态机：

    pending ──> asking ──> ready ──> done
       │           ▲         │
       │           │         │
       └───────────┴─────────┘  (低置信度会挂一个 need_human 标记，
                                  状态不变，但会在前端标黄等人工确认)

  pending : 刚进来 / 已识别但信息不全
  asking  : 缺必需槽位，Agent 需要追问用户
  ready   : 槽位齐 + 置信度够，可以执行（可一键分拣）
  done    : 人工/自动分拣完成
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class Intent(str, Enum):
    RETURN = "退货退款"
    EXCHANGE = "换货"
    COMPLAINT = "投诉"
    LOGISTICS = "物流查询"
    INVOICE = "发票"
    URGE = "催单"
    CONSULT = "咨询"
    SUGGESTION = "建议"
    OTHER = "其他"


class GoalStatus(str, Enum):
    PENDING = "pending"
    ASKING = "asking"
    READY = "ready"
    DONE = "done"


class TicketStatus(str, Enum):
    PENDING = "pending"
    ASKING = "asking"
    READY = "ready"
    DONE = "done"


# 状态机合法迁移表：写成数据而不是 if-else，前端也能直接读去做 UI 置灰。
#
# 这里有一条刻意的约束：pending 不能直接跳到 done。
# 因为 pending 代表"还没被人工确认"（低置信度目标会停在这里），
# 如果允许 pending → done，人工确认这道闸门就形同虚设 ——
# 系统可以在没人看过的情况下把低置信度工单标记成"已分拣"。
# 对于人机协同系统，这是比"多一次点击"严重得多的问题。
ALLOWED_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.PENDING: {GoalStatus.ASKING, GoalStatus.READY},
    GoalStatus.ASKING: {GoalStatus.READY, GoalStatus.PENDING},
    GoalStatus.READY: {GoalStatus.DONE, GoalStatus.ASKING},
    GoalStatus.DONE: {GoalStatus.READY},  # 允许撤销分拣，人总会手抖
}


# --------------------------------------------------------------------------- #
# 槽位定义：每个意图需要哪些槽位，哪些是"必需"的
# --------------------------------------------------------------------------- #
SLOT_SCHEMA: dict[Intent, list[str]] = {
    Intent.RETURN: ["订单号", "商品", "退款原因"],
    Intent.EXCHANGE: ["订单号", "商品", "换货规格"],
    Intent.COMPLAINT: ["订单号", "投诉对象", "投诉事由"],
    Intent.LOGISTICS: ["订单号", "承运商"],
    Intent.INVOICE: ["订单号", "发票类型", "抬头"],
    Intent.URGE: ["订单号", "期望时效"],
    Intent.CONSULT: ["咨询主题"],
    Intent.SUGGESTION: ["建议主题"],
    Intent.OTHER: [],
}

REQUIRED_SLOTS: dict[Intent, list[str]] = {
    Intent.RETURN: ["订单号"],
    Intent.EXCHANGE: ["订单号", "换货规格"],
    Intent.COMPLAINT: ["投诉对象"],
    Intent.LOGISTICS: ["订单号"],
    Intent.INVOICE: ["订单号", "发票类型"],
    Intent.URGE: ["订单号"],
    Intent.CONSULT: [],
    Intent.SUGGESTION: [],
    Intent.OTHER: [],
}

# 缺槽位时，Agent 该问什么
SLOT_QUESTIONS: dict[str, str] = {
    "订单号": "麻烦提供一下订单号（可在订单详情页复制），我马上为您处理～",
    "商品": "请问涉及的是哪件商品呢？",
    "退款原因": "方便说下退货原因吗？（质量问题 / 不喜欢 / 拍错 等）",
    "换货规格": "希望换成什么规格或型号呢？",
    "投诉对象": "请问您要投诉的是商家、客服还是物流？",
    "投诉事由": "能简单描述一下具体情况吗？",
    "承运商": "请问是哪家快递公司呢？",
    "发票类型": "需要开专票还是普票？",
    "抬头": "请提供发票抬头和税号，谢谢～",
    "期望时效": "希望什么时候收到呢？",
    "咨询主题": "您具体想咨询哪个方面？",
    "建议主题": "您的建议是关于哪方面的？",
}

# 前端配色（后端统一下发，避免前后端各写一套色板）
INTENT_COLOR: dict[Intent, str] = {
    Intent.RETURN: "#38bdf8",
    Intent.EXCHANGE: "#22d3ee",
    Intent.COMPLAINT: "#fb7185",
    Intent.LOGISTICS: "#a78bfa",
    Intent.INVOICE: "#fbbf24",
    Intent.URGE: "#f97316",
    Intent.CONSULT: "#34d399",
    Intent.SUGGESTION: "#e879f9",
    Intent.OTHER: "#94a3b8",
}


# --------------------------------------------------------------------------- #
# 原子结构
# --------------------------------------------------------------------------- #
class SlotValue(BaseModel):
    """一个槽位：值 + 它是怎么来的 + 有多可信。

    为什么要记录 source？因为"用户明确说的"和"模型猜的"在业务上待遇完全不同：
    前者可以直接执行，后者必须人工确认。很多项目只存值，事后再也解释不清。
    """

    name: str
    value: str | None = None
    required: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Literal["text", "inferred", "human"] = "text"

    @computed_field
    @property
    def filled(self) -> bool:
        return bool(self.value)


class TraceStep(BaseModel):
    """状态机每走一步都留痕，前端能画出 Agent 的思考链路 —— 可解释性是卖点。"""

    node: str
    label: str
    detail: str = ""
    engine: str = "rule"
    ms: int = 0
    ts: str = Field(default_factory=now_iso)


class Goal(BaseModel):
    id: str = Field(default_factory=lambda: new_id("goal"))
    segment: str = Field(description="拆出来的原始句子片段，可追溯到用户原话")
    intent: Intent
    intent_scores: dict[str, float] = Field(default_factory=dict)
    slots: list[SlotValue] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: GoalStatus = GoalStatus.PENDING
    need_human: bool = False
    human_action: Literal["approve", "correct", "reject"] | None = None
    questions: list[str] = Field(default_factory=list)
    reason: str = ""
    updated_at: str = Field(default_factory=now_iso)

    # ---- 双引擎证据（LLM 与规则各自打的分，用于交叉验证与可解释性） -------- #
    engine: str = "rule"
    llm_confidence: float | None = None
    rule_confidence: float | None = None
    agreement: bool | None = None

    # ---- 派生属性 ---------------------------------------------------------- #
    @computed_field
    @property
    def missing_required(self) -> list[str]:
        return [s.name for s in self.slots if s.required and not s.value]

    @computed_field
    @property
    def slot_fill_rate(self) -> float:
        if not self.slots:
            return 1.0
        return round(sum(1 for s in self.slots if s.value) / len(self.slots), 3)

    @computed_field
    @property
    def intent_color(self) -> str:
        return INTENT_COLOR.get(self.intent, "#94a3b8")

    @computed_field
    @property
    def next_statuses(self) -> list[str]:
        return sorted(s.value for s in ALLOWED_TRANSITIONS[self.status])

    # ---- 行为 -------------------------------------------------------------- #
    def can_transition(self, target: GoalStatus) -> bool:
        return target in ALLOWED_TRANSITIONS[self.status]

    def transition(self, target: GoalStatus) -> None:
        if not self.can_transition(target):
            raise ValueError(
                f"非法状态迁移：{self.status.value} -> {target.value}"
                f"（允许：{self.next_statuses}）"
            )
        self.status = target
        self.updated_at = now_iso()

    def slot_map(self) -> dict[str, str]:
        return {s.name: s.value for s in self.slots if s.value}

    def set_slots(
        self,
        values: dict[str, str],
        source: Literal["text", "human"],
        confidence_map: dict[str, float] | None = None,
    ) -> None:
        wanted = SLOT_SCHEMA.get(self.intent, [])
        required = set(REQUIRED_SLOTS.get(self.intent, []))
        existing = {s.name: s for s in self.slots}
        confidence_map = confidence_map or {}
        merged: list[SlotValue] = []
        for name in wanted:
            old = existing.get(name)
            if name in values and values[name]:
                fallback = max(0.6, old.confidence if old else 0.6)
                conf = confidence_map.get(name, 1.0 if source == "human" else fallback)
                merged.append(
                    SlotValue(
                        name=name,
                        value=str(values[name]).strip(),
                        required=name in required,
                        confidence=round(min(1.0, max(0.0, conf)), 3),
                        source=source,
                    )
                )
            elif old is not None:
                old.required = name in required
                merged.append(old)
            else:
                merged.append(SlotValue(name=name, required=name in required, source="text", confidence=0.0))
        self.slots = merged
        self.questions = [
            SLOT_QUESTIONS.get(n, f"请补充「{n}」") for n in self.missing_required
        ]
        self.updated_at = now_iso()


class Ticket(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tkt"))
    customer: str = "匿名用户"
    channel: str = "在线客服"
    text: str
    created_at: str = Field(default_factory=now_iso)
    status: TicketStatus = TicketStatus.PENDING
    engine: str = "rule"
    goals: list[Goal] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    analyzed_at: str | None = None
    ready_at: str | None = None
    done_at: str | None = None
    priority: Literal["低", "中", "高"] = "中"

    @model_validator(mode="after")
    def _sync_status(self) -> "Ticket":
        """工单状态始终由它的目标推导出来，杜绝"工单说 done、目标还没执行"的脏数据。"""
        if not self.goals:
            return self
        if all(g.status == GoalStatus.DONE for g in self.goals):
            self.status = TicketStatus.DONE
        elif any(g.status == GoalStatus.ASKING for g in self.goals):
            self.status = TicketStatus.ASKING
        elif all(g.status in (GoalStatus.READY, GoalStatus.DONE) for g in self.goals):
            self.status = TicketStatus.READY
        else:
            self.status = TicketStatus.PENDING
        return self

    @computed_field
    @property
    def confidence(self) -> float:
        if not self.goals:
            return 0.0
        return round(sum(g.confidence for g in self.goals) / len(self.goals), 3)

    @computed_field
    @property
    def need_human(self) -> bool:
        return any(g.need_human for g in self.goals)

    @computed_field
    @property
    def intents(self) -> list[str]:
        return [g.intent.value for g in self.goals]

    @computed_field
    @property
    def summary(self) -> str:
        if not self.goals:
            return "尚未识别"
        return " + ".join(self.intents)

    def refreshed(self) -> "Ticket":
        """重新走一遍校验（触发 _sync_status），用于落库前纠正脏状态。"""
        return Ticket.model_validate(
            {name: getattr(self, name) for name in Ticket.model_fields}
        )


# --------------------------------------------------------------------------- #
# API 出入参
# --------------------------------------------------------------------------- #
class TicketCreate(BaseModel):
    text: str = Field(min_length=2, max_length=2000)
    customer: str = "匿名用户"
    channel: str = "在线客服"
    priority: Literal["低", "中", "高"] = "中"
    auto_analyze: bool = True


class AnalyzeRequest(BaseModel):
    ticket_id: str | None = None
    text: str | None = None
    engine: Literal["auto", "llm", "rule"] = "auto"

    @model_validator(mode="after")
    def _need_one(self) -> "AnalyzeRequest":
        if not self.ticket_id and not self.text:
            raise ValueError("ticket_id 和 text 至少给一个")
        return self


class ConfirmRequest(BaseModel):
    ticket_id: str
    goal_id: str
    action: Literal["approve", "correct", "reject"] = "approve"
    intent: Intent | None = None
    slots: dict[str, str] | None = None
    operator: str = "human-agent"


class TransitionRequest(BaseModel):
    ticket_id: str
    goal_id: str | None = None
    target: GoalStatus
    operator: str = "human-agent"


class EvalRequest(BaseModel):
    engine: Literal["auto", "llm", "rule"] = "rule"
    limit: int | None = None


# --------------------------------------------------------------------------- #
# 统计
# --------------------------------------------------------------------------- #
class IntentBucket(BaseModel):
    intent: str
    count: int
    color: str
    avg_confidence: float


class StatusBucket(BaseModel):
    status: str
    count: int


class AccuracyReport(BaseModel):
    auto_ready: int = 0
    need_human: int = 0
    human_approved: int = 0
    human_corrected: int = 0
    human_rejected: int = 0

    @computed_field
    @property
    def auto_rate(self) -> float:
        total = self.auto_ready + self.need_human
        return round(self.auto_ready / total, 4) if total else 0.0

    @computed_field
    @property
    def human_accuracy(self) -> float:
        reviewed = self.human_approved + self.human_corrected + self.human_rejected
        return round(self.human_approved / reviewed, 4) if reviewed else 1.0

    @computed_field
    @property
    def overall(self) -> float:
        """综合分拣准确率 = 自动分拣那部分 + 人工兜底那部分。

        自动分拣的样本我们假设正确（用一个先验 cap 住，不能吹成 100%），
        人工复核过的样本用真实的对错比。这样这个数字才不会被"多转人工"刷高。
        """
        total = self.auto_ready + self.human_approved + self.human_corrected + self.human_rejected
        if not total:
            return 0.0
        assumed_auto_correct = self.auto_ready * 0.95
        correct = assumed_auto_correct + self.human_approved
        return round(correct / total, 4)


class StatsResponse(BaseModel):
    total_tickets: int
    total_goals: int
    today_tickets: int
    avg_confidence: float
    needs_attention: int
    intent_distribution: list[IntentBucket]
    status_distribution: list[StatusBucket]
    top_slots_missing: list[dict[str, Any]]
    accuracy: AccuracyReport
    engine: str
