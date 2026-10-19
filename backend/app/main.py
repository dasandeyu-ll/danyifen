"""FastAPI 应用：工单 CRUD + 两阶段分析 + 人工确认 + 看板统计。

接口一览
--------
GET    /api/health                     健康检查
GET    /api/meta                       意图词典 / 槽位 schema / 阈值 / 状态机图（前端配置化渲染）
GET    /api/tickets                    工单列表（可按状态、关键词过滤）
POST   /api/tickets                    新建工单（可自动触发分析）
GET    /api/tickets/{id}               工单详情
DELETE /api/tickets/{id}               删除工单
POST   /api/tickets/analyze            两阶段分析：意图分类 → 槽位抽取
POST   /api/tickets/confirm            人工确认：approve / correct / reject
POST   /api/tickets/advance            状态机推进一步（ready → done 等）
POST   /api/tickets/{id}/dispatch      一键分拣：该工单下所有 ready 目标 → done
POST   /api/tickets/dispatch-all       演示用：全部就绪目标一次性分拣
GET    /api/stats                      看板统计
POST   /api/seed                       灌入演示数据
POST   /api/eval/run                   跑 200 条评测集，返回意图准确率 / 槽位 F1
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .agent.graph import LANGGRAPH_AVAILABLE, graph_mermaid, run_agent
from .agent.llm import USAGE, analyzer
from .config import get_settings
from .schemas import (
    AnalyzeRequest,
    ConfirmRequest,
    EvalRequest,
    Goal,
    GoalStatus,
    INTENT_COLOR,
    Intent,
    REQUIRED_SLOTS,
    SLOT_SCHEMA,
    StatsResponse,
    Ticket,
    TicketCreate,
    TicketStatus,
    TransitionRequest,
    new_id,
    now_iso,
)
from .seed import DEMO_TICKETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("api")

_SETTINGS = get_settings()
_LAST_EVAL: dict[str, Any] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    mode = "DeepSeek 大模型" if _SETTINGS.llm_enabled else "内置规则引擎（未配置 DEEPSEEK_API_KEY）"
    logger.info("=" * 68)
    logger.info("  %s 已启动", _SETTINGS.app_name)
    logger.info("  分析引擎    : %s", mode)
    logger.info("  LangGraph   : %s", "已加载" if LANGGRAPH_AVAILABLE else "未安装（使用内置线性执行器）")
    logger.info("  置信度阈值  : %.2f", _SETTINGS.confidence_threshold)
    logger.info("  文档地址    : http://127.0.0.1:8000/docs")
    logger.info("=" * 68)
    yield


app = FastAPI(
    title=_SETTINGS.app_name,
    description="基于目标识别的智能工单分拣系统 —— 单句多目标拆分 · 意图多标签 · 槽位抽取 · 人机协同分拣",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_SETTINGS.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app": _SETTINGS.app_name,
        "llm_enabled": _SETTINGS.llm_enabled,
        "langgraph": LANGGRAPH_AVAILABLE,
        "llm_usage": USAGE.as_dict(),
    }


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    """把后端的"领域配置"下发给前端，避免前后端各写一份意图列表/色板/槽位表。"""
    return {
        "app_name": _SETTINGS.app_name,
        "llm_enabled": _SETTINGS.llm_enabled,
        "langgraph": LANGGRAPH_AVAILABLE,
        "confidence_threshold": _SETTINGS.confidence_threshold,
        "margin_threshold": _SETTINGS.margin_threshold,
        "intents": [
            {
                "name": i.value,
                "color": INTENT_COLOR[i],
                "slots": SLOT_SCHEMA[i],
                "required_slots": REQUIRED_SLOTS[i],
            }
            for i in Intent
        ],
        "status_flow": [s.value for s in GoalStatus],
        "graph_mermaid": graph_mermaid(),
    }


# --------------------------------------------------------------------------- #
# 工单 CRUD
# --------------------------------------------------------------------------- #
def _find_ticket(ticket_id: str) -> Ticket:
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"工单不存在：{ticket_id}")
    return ticket


@app.get("/api/tickets", response_model=list[Ticket])
def list_tickets(
    status: TicketStatus | None = None,
    q: str | None = Query(default=None, description="按留言内容或客户名模糊搜索"),
    limit: int = Query(default=120, ge=1, le=500),
) -> list[Ticket]:
    return db.list_tickets(status=status, keyword=q, limit=limit)


@app.post("/api/tickets", response_model=Ticket, status_code=201)
async def create_ticket(payload: TicketCreate) -> Ticket:
    ticket = Ticket(
        customer=payload.customer,
        channel=payload.channel,
        text=payload.text,
        priority=payload.priority,
    )
    db.save_ticket(ticket, log_event="created")
    if payload.auto_analyze:
        ticket = await _analyze_ticket(ticket, "auto")
    return ticket


@app.get("/api/tickets/{ticket_id}", response_model=Ticket)
def get_ticket(ticket_id: str) -> Ticket:
    return _find_ticket(ticket_id)


@app.delete("/api/tickets/{ticket_id}")
def remove_ticket(ticket_id: str) -> dict[str, Any]:
    ok = db.delete_ticket(ticket_id)
    if not ok:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"deleted": ticket_id}


@app.get("/api/tickets/{ticket_id}/events")
def ticket_events(ticket_id: str) -> list[dict[str, Any]]:
    _find_ticket(ticket_id)
    return db.list_events(ticket_id)


# --------------------------------------------------------------------------- #
# 分析
# --------------------------------------------------------------------------- #
async def _analyze_ticket(ticket: Ticket, engine: str) -> Ticket:
    goals, trace, used_engine = await run_agent(ticket.text, engine)
    ticket.goals = goals
    ticket.trace = trace
    ticket.engine = used_engine
    ticket.analyzed_at = now_iso()
    db.save_ticket(ticket, log_event="analyzed")
    return db.get_ticket(ticket.id) or ticket


@app.post("/api/tickets/analyze", response_model=Ticket)
async def analyze_ticket(payload: AnalyzeRequest) -> Ticket:
    """两阶段分析。传 ticket_id 就重跑已有工单，传 text 就直接分析一段文本（不落库）。"""
    if payload.ticket_id:
        ticket = _find_ticket(payload.ticket_id)
        return await _analyze_ticket(ticket, payload.engine)

    ticket = Ticket(text=payload.text or "", customer="临时试算", channel="试算台")
    goals, trace, used_engine = await run_agent(ticket.text, payload.engine)
    ticket.goals = goals
    ticket.trace = trace
    ticket.engine = used_engine
    ticket.analyzed_at = now_iso()
    return ticket.refreshed()  # 试算不落库，避免污染统计


# --------------------------------------------------------------------------- #
# 人工确认 / 状态机推进
# --------------------------------------------------------------------------- #
@app.post("/api/tickets/confirm", response_model=Ticket)
def confirm_goal(payload: ConfirmRequest) -> Ticket:
    """人工确认：把 low-confidence 的目标"捞"回正常流程。

    approve : 采纳 Agent 的判定 → 目标置为 ready（缺槽位则 asking）
    correct : 人工改成别的意图 / 补槽位 → 记录 human_action=correct，用于算真实准确率
    reject  : 判定为误识别 → 目标退回 pending，重新识别
    """
    found = db.get_goal(payload.ticket_id, payload.goal_id)
    if not found:
        raise HTTPException(status_code=404, detail="工单或目标不存在")
    ticket, goal = found

    if payload.action == "reject":
        goal.human_action = "reject"
        goal.need_human = False
        goal.reason = f"{payload.operator} 判定为误识别，退回重识别"
        if goal.status != GoalStatus.PENDING:
            goal.transition(GoalStatus.PENDING)
        db.save_ticket(ticket, log_event="confirm_reject")
        return db.get_ticket(ticket.id) or ticket

    if payload.action == "correct":
        goal.human_action = "correct"
        if payload.intent and payload.intent != goal.intent:
            goal.intent = payload.intent
            goal.intent_scores = {payload.intent.value: 1.0}
            goal.slots = []
            goal.set_slots({}, source="human")
        goal.engine = "human"
        goal.reason = f"{payload.operator} 人工修正：{goal.intent.value}"
    else:
        goal.human_action = "approve"
        goal.reason = f"{payload.operator} 确认采纳 Agent 判定"

    if payload.slots:
        goal.set_slots(payload.slots, source="human")
    goal.need_human = False
    goal.confidence = 1.0
    goal.updated_at = now_iso()

    target = GoalStatus.ASKING if goal.missing_required else GoalStatus.READY
    if goal.status != target:
        goal.transition(target)

    db.save_ticket(ticket, log_event=f"confirm_{payload.action}")
    return db.get_ticket(ticket.id) or ticket


@app.post("/api/tickets/advance", response_model=Ticket)
def advance(payload: TransitionRequest) -> Ticket:
    """状态机推进一步。

    注意这里**不**让调用方随便改状态：每一步都要过 Goal.can_transition 校验，
    并且 ready 之前必须把必需槽位补齐。非法迁移返回 409 而不是静默忽略 ——
    状态机要是能被绕过，它就不是状态机了。
    """
    ticket = _find_ticket(payload.ticket_id)

    targets = (
        [g for g in ticket.goals if g.id == payload.goal_id] if payload.goal_id else list(ticket.goals)
    )
    if not targets:
        raise HTTPException(status_code=404, detail="目标不存在")

    errors: list[str] = []
    moved = 0
    for goal in targets:
        if goal.status == payload.target:
            continue
        if not goal.can_transition(payload.target):
            errors.append(f"{goal.intent.value}: {goal.status.value} → {payload.target.value} 非法迁移")
            continue
        if payload.target == GoalStatus.READY and goal.missing_required:
            errors.append(f"{goal.intent.value}: 仍缺必需槽位 {'、'.join(goal.missing_required)}")
            continue
        goal.transition(payload.target)
        goal.updated_at = now_iso()
        if payload.target == GoalStatus.DONE:
            goal.human_action = goal.human_action or "approve"
        moved += 1

    if errors and moved == 0:
        raise HTTPException(status_code=409, detail="；".join(errors))

    if payload.target == GoalStatus.DONE and all(g.status == GoalStatus.DONE for g in ticket.goals):
        ticket.done_at = now_iso()
    if payload.target == GoalStatus.READY:
        ticket.ready_at = ticket.ready_at or now_iso()

    db.save_ticket(ticket, log_event=f"advance_{payload.target.value}")
    result = db.get_ticket(ticket.id) or ticket
    if errors:
        # 部分成功时把失败原因挂到日志里，前端可通过 events 接口看到
        logger.warning("部分目标未能迁移：%s", "；".join(errors))
    return result


@app.post("/api/tickets/{ticket_id}/dispatch", response_model=Ticket)
def dispatch_ticket(ticket_id: str, operator: str = "auto-dispatcher") -> Ticket:
    """一键分拣：把一个工单下所有 ready 目标推到 done。

    这是"最后一公里"的自动化 —— Agent 判对了就不用人工点，
    只有 need_human 的目标才需要人。
    """
    ticket = _find_ticket(ticket_id)
    moved = 0
    for goal in ticket.goals:
        if goal.status == GoalStatus.READY:
            goal.transition(GoalStatus.DONE)
            goal.human_action = goal.human_action or "approve"
            goal.updated_at = now_iso()
            moved += 1
    if moved:
        ticket.done_at = now_iso()
        db.save_ticket(ticket, log_event="dispatch")
    result = db.get_ticket(ticket.id) or ticket
    logger.info("%s 分拣了 %s 个目标（工单 %s）", operator, moved, ticket.id)
    return result


@app.post("/api/tickets/dispatch-all")
def dispatch_all() -> dict[str, Any]:
    moved = 0
    scanned = 0
    for ticket in db.list_tickets(limit=500):
        scanned += 1
        changed = False
        for goal in ticket.goals:
            if goal.status == GoalStatus.READY:
                goal.transition(GoalStatus.DONE)
                goal.human_action = goal.human_action or "approve"
                goal.updated_at = now_iso()
                moved += 1
                changed = True
        if changed:
            ticket.done_at = now_iso()
            db.save_ticket(ticket, log_event="dispatch_all")
    return {"scanned": scanned, "dispatched_goals": moved}


# --------------------------------------------------------------------------- #
# 统计
# --------------------------------------------------------------------------- #
@app.get("/api/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    return db.build_stats(engine="deepseek" if _SETTINGS.llm_enabled else "rule")


# --------------------------------------------------------------------------- #
# 演示数据 / 评测
# --------------------------------------------------------------------------- #
@app.post("/api/seed")
async def seed(reset: bool = Query(default=True, description="是否先清空现有数据")) -> dict[str, Any]:
    if reset:
        db.truncate_all()
    created = 0
    for item in DEMO_TICKETS:
        ticket = Ticket(
            customer=item["customer"],
            channel=item["channel"],
            text=item["text"],
            priority=item.get("priority", "中"),
        )
        db.save_ticket(ticket, log_event="created")
        await _analyze_ticket(ticket, "auto")
        created += 1
    logger.info("已灌入 %s 条演示工单", created)
    return {"created": created}


@app.post("/api/eval/run")
def run_eval(payload: EvalRequest) -> dict[str, Any]:
    """跑自建评测集。默认走规则引擎（快、确定性），也可以切 llm 真实评测。"""
    global _LAST_EVAL
    from .eval.run_eval import run_evaluation

    report = run_evaluation(engine=payload.engine, limit=payload.limit)
    _LAST_EVAL = report
    return report


@app.get("/api/eval/last")
def last_eval() -> dict[str, Any]:
    return _LAST_EVAL or {"message": "还没跑过评测，POST /api/eval/run 触发一次"}


def get_last_eval() -> dict[str, Any] | None:
    return _LAST_EVAL
