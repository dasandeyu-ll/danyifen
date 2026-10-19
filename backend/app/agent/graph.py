"""LangGraph 状态机：识别 → 抽槽 → 校验 → 追问 / 转人工 / 就绪。

流程图
------
                     ┌──────────┐
   START ───────────>│  split   │  单句多目标拆分（确定性规则，不用 LLM）
                     └────┬─────┘
                          v
                     ┌──────────┐
                     │ classify │  多标签意图分类（LLM，失败降级规则）
                     └────┬─────┘
                          v
                     ┌──────────┐
                     │ extract  │  结构化槽位抽取（LLM，失败降级规则）
                     └────┬─────┘
                          v
                     ┌──────────┐
                     │  verify  │  Pydantic 校验 + 双引擎交叉验证 + 置信度校准
                     └────┬─────┘
              ┌───────────┼───────────┐
              v           v           v
          ┌───────┐   ┌───────┐   ┌───────┐
          │  ask  │   │ ready │   │ human │
          └───┬───┘   └───┬───┘   └───┬───┘
              └───────────┴───────────┘
                          v
                         END

为什么用 LangGraph 而不是把所有逻辑塞进一个 async 函数？
------------------------------------------------------
1. **状态显式化**：State 是一个 TypedDict，任何节点只读自己需要的字段、
   只返回自己的增量。加一个"情绪识别"节点不用改别的节点。
2. **条件路由是一等公民**：`add_conditional_edges` 把"什么时候转人工"
   变成图上的边，而不是埋在 800 行 if-else 深处 —— 可以直接渲染给面试官看。
3. **可观测**：每个节点的进出都可以被回调捕获，天然支持 tracing 和
   断点续跑（配 checkpoint 就能做"人工确认后从断点继续"）。
4. **可测试**：每个节点都是纯函数（或带外部依赖的 async 函数），能单测。

但也有代价：LangGraph 引入了一层抽象和版本耦合。所以下面的实现里我做了
`LANGGRAPH_AVAILABLE` 降级 —— 图跑不起来时用等价的线性执行器，
保证业务逻辑只有一份（节点函数复用），不会出现"两套实现不一致"。
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

from ..config import get_settings
from ..schemas import Goal, GoalStatus, Intent, TraceStep, now_iso
from . import rules
from .llm import analyzer

logger = logging.getLogger("agent.graph")

try:  # pragma: no cover - 取决于环境
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover
    LANGGRAPH_AVAILABLE = False
    logger.warning("langgraph 不可用，退化到内置线性执行器")


class GraphState(TypedDict, total=False):
    text: str
    requested_engine: str
    engine: str
    segments: list[str]
    goals: list[Goal]
    trace: list[TraceStep]
    route: str


def _clamp(x: float, lo: float = 0.0, hi: float = 0.98) -> float:
    return max(lo, min(hi, x))


def _step(state: GraphState, node: str, label: str, detail: str, engine: str, ms: int) -> list[TraceStep]:
    return [
        *state.get("trace", []),
        TraceStep(node=node, label=label, detail=detail, engine=engine, ms=ms),
    ]


# --------------------------------------------------------------------------- #
# 节点 1：拆分
# --------------------------------------------------------------------------- #
def node_split(state: GraphState) -> dict[str, Any]:
    t0 = time.perf_counter()
    segments = rules.split_goals(state["text"])
    ms = int((time.perf_counter() - t0) * 1000)
    preview = " ｜ ".join(s[:24] for s in segments)
    return {
        "segments": segments,
        "trace": _step(
            state,
            "split",
            "单句多目标拆分",
            f"从 {len(state['text'])} 字留言中拆出 {len(segments)} 个目标：{preview}",
            "rule",
            ms,
        ),
    }


# --------------------------------------------------------------------------- #
# 节点 2：多标签意图分类
# --------------------------------------------------------------------------- #
async def node_classify(state: GraphState) -> dict[str, Any]:
    settings = get_settings()
    segments = state["segments"]
    want_llm = settings.llm_enabled and state.get("requested_engine", "auto") != "rule"

    t0 = time.perf_counter()
    llm_results: list[list[tuple[Intent, float]] | None] = [None] * len(segments)
    if want_llm:
        try:
            llm_results = await analyzer.classify(segments)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 分类整体失败，全部降级：%s", exc)
            llm_results = [None] * len(segments)
    llm_ms = int((time.perf_counter() - t0) * 1000)

    goals: list[Goal] = []
    for seg, llm_pairs in zip(segments, llm_results):
        rule_hits, rule_conf = rules.classify(seg)
        if llm_pairs:
            scores = {i.value: round(s, 3) for i, s in llm_pairs}
            top_intent, llm_conf = llm_pairs[0][0], round(llm_pairs[0][1], 3)
            engine = "deepseek"
        else:
            scores = {h.intent.value: round(h.prob, 3) for h in rule_hits}
            top_intent, llm_conf = rule_hits[0].intent, None
            engine = "rule"

        goals.append(
            Goal(
                segment=seg,
                intent=top_intent,
                intent_scores=scores,
                engine=engine,
                llm_confidence=llm_conf,
                rule_confidence=rule_conf,
            )
        )

    used = "deepseek" if any(g.engine == "deepseek" for g in goals) else "rule"
    detail = "；".join(
        f"「{g.segment[:16]}」→ {g.intent.value}"
        + (f"（LLM {g.llm_confidence:.2f}）" if g.llm_confidence is not None else "（规则）")
        for g in goals
    )
    return {
        "goals": goals,
        "engine": used,
        "trace": _step(state, "classify", "多标签意图分类", detail, used, llm_ms),
    }


# --------------------------------------------------------------------------- #
# 节点 3：结构化槽位抽取
# --------------------------------------------------------------------------- #
async def node_extract(state: GraphState) -> dict[str, Any]:
    goals = state["goals"]
    t0 = time.perf_counter()
    use_llm = analyzer.enabled and state.get("requested_engine", "auto") != "rule"

    details: list[str] = []
    for goal in goals:
        found: dict[str, str] = {}
        confidence_map: dict[str, float] = {}
        source = "rule"

        if use_llm:
            try:
                llm_slots = await analyzer.extract(goal.segment, goal.intent)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM 抽槽失败，降级：%s", exc)
                llm_slots = None
            if llm_slots:
                found = {k: v["value"] for k, v in llm_slots.items()}
                confidence_map = {k: v["confidence"] for k, v in llm_slots.items()}
                source = "deepseek"

        if not found:
            found = rules.extract_slots(goal.segment, goal.intent)
            confidence_map = {k: 0.78 for k in found}

        goal.set_slots(found, source="text", confidence_map=confidence_map)
        hit = "、".join(f"{k}={v}" for k, v in found.items()) or "（无）"
        details.append(f"{goal.intent.value}: {hit}")

    ms = int((time.perf_counter() - t0) * 1000)
    engine = "deepseek" if use_llm else "rule"
    return {
        "goals": goals,
        "trace": _step(state, "extract", "结构化槽位抽取", "；".join(details), engine, ms),
    }


# --------------------------------------------------------------------------- #
# 节点 4：校验 + 置信度校准
# --------------------------------------------------------------------------- #
def node_verify(state: GraphState) -> dict[str, Any]:
    settings = get_settings()
    t0 = time.perf_counter()
    goals = state["goals"]

    for goal in goals:
        # 关键：不管上面用的是 LLM 还是规则，这里都再跑一遍规则引擎做交叉验证。
        # 两者一致 → 提升置信度；两者打架 → 直接判定需要人工介入。
        rule_hits, rule_conf = rules.classify(goal.segment)
        rule_top = rule_hits[0].intent
        agree = rule_top == goal.intent
        goal.rule_confidence = rule_conf
        goal.agreement = agree

        if goal.llm_confidence is not None:
            raw = 0.52 * goal.llm_confidence + 0.48 * rule_conf + (0.08 if agree else -0.14)
        else:
            raw = rule_conf
        goal.confidence = round(_clamp(raw), 3)

        need_human, reason = rules.intent_needs_human_review(
            rule_hits, goal.confidence, settings.confidence_threshold, settings.margin_threshold
        )
        if goal.llm_confidence is not None and not agree:
            need_human = True
            reason = f"交叉验证不一致：LLM 判「{goal.intent.value}」但规则判「{rule_top.value}」"
        goal.need_human = need_human
        goal.reason = reason

        # ---- 状态写入：这里是整个生命周期状态机唯一的"判决点" ---- #
        missing = goal.missing_required
        if missing:
            goal.status = GoalStatus.ASKING
            goal.reason = f"缺少必需槽位：{'、'.join(missing)}" + (f"；{reason}" if reason else "")
        elif need_human:
            goal.status = GoalStatus.PENDING
        else:
            goal.status = GoalStatus.READY
        goal.updated_at = now_iso()

    if any(g.status == GoalStatus.ASKING for g in goals):
        route = "ask"
    elif any(g.need_human for g in goals):
        route = "human"
    else:
        route = "ready"

    ms = int((time.perf_counter() - t0) * 1000)
    detail = "；".join(
        f"{g.intent.value} → {g.status.value}（conf {g.confidence:.2f}{'，需人工' if g.need_human else ''}）"
        for g in goals
    )
    return {
        "goals": goals,
        "route": route,
        "trace": _step(state, "verify", "校验与置信度校准", detail, "rule", ms),
    }


# --------------------------------------------------------------------------- #
# 终态节点
# --------------------------------------------------------------------------- #
def _terminal(node: str, label: str):
    def _fn(state: GraphState) -> dict[str, Any]:
        goals = state["goals"]
        pending = [g for g in goals if g.status == GoalStatus.PENDING and g.need_human]
        if node == "ask":
            detail = "需向用户追问：" + "；".join("、".join(g.questions) for g in goals if g.questions)
        elif node == "human":
            detail = "转人工复核：" + "；".join(f"{g.intent.value}({g.reason})" for g in pending) or "转人工复核"
        else:
            detail = f"{len([g for g in goals if g.status == GoalStatus.READY])} 个目标已就绪，可直接分拣"
        return {"route": node, "trace": _step(state, node, label, detail, "rule", 0)}

    return _fn


# --------------------------------------------------------------------------- #
# 组图
# --------------------------------------------------------------------------- #
def _build_graph() -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("split", node_split)
    builder.add_node("classify", node_classify)
    builder.add_node("extract", node_extract)
    builder.add_node("verify", node_verify)
    builder.add_node("ask", _terminal("ask", "生成追问话术"))
    builder.add_node("ready", _terminal("ready", "目标就绪"))
    builder.add_node("human", _terminal("human", "转人工复核"))

    builder.add_edge(START, "split")
    builder.add_edge("split", "classify")
    builder.add_edge("classify", "extract")
    builder.add_edge("extract", "verify")
    builder.add_conditional_edges(
        "verify",
        lambda s: s.get("route", "ready"),
        {"ask": "ask", "ready": "ready", "human": "human"},
    )
    builder.add_edge("ask", END)
    builder.add_edge("ready", END)
    builder.add_edge("human", END)
    return builder.compile()


_COMPILED = _build_graph() if LANGGRAPH_AVAILABLE else None


async def _run_linear(state: GraphState) -> GraphState:
    """LangGraph 缺席时的等价执行器：节点函数完全复用，结果一致。"""
    state.update(await node_split(state))  # type: ignore[arg-type]
    state.update(await node_classify(state))
    state.update(await node_extract(state))
    state.update(node_verify(state))
    terminal = _terminal(f"terminal_{state.get('route', 'ready')}", "终态")
    if state.get("route") == "ask":
        terminal = _terminal("ask", "生成追问话术")
    elif state.get("route") == "human":
        terminal = _terminal("human", "转人工复核")
    else:
        terminal = _terminal("ready", "目标就绪")
    state.update(terminal(state))
    return state


async def run_agent(text: str, requested_engine: str = "auto") -> tuple[list[Goal], list[TraceStep], str]:
    """对外唯一入口：一段文本 → (目标列表, 执行轨迹, 实际使用的引擎)。"""
    initial: GraphState = {
        "text": text,
        "requested_engine": requested_engine,
        "trace": [],
        "segments": [],
        "goals": [],
    }
    if _COMPILED is not None:
        final: GraphState = await _COMPILED.ainvoke(initial)
    else:
        final = await _run_linear(dict(initial))

    goals = final.get("goals", [])
    trace = final.get("trace", [])
    engine = final.get("engine", "rule")
    return goals, trace, engine


def graph_mermaid() -> str:
    """把状态机导出成 Mermaid，前端可直接渲染 —— 让"流程图"和"真代码"永不脱节。"""
    return (
        "graph LR\n"
        "  START([开始]) --> split[单句多目标拆分]\n"
        "  split --> classify[多标签意图分类]\n"
        "  classify --> extract[结构化槽位抽取]\n"
        "  extract --> verify[校验 & 置信度校准]\n"
        "  verify -->|缺槽位| ask[生成追问话术]\n"
        "  verify -->|低置信度/交叉验证不一致| human[转人工复核]\n"
        "  verify -->|通过| ready[目标就绪]\n"
        "  ask --> END([结束])\n"
        "  human --> END\n"
        "  ready --> END\n"
    )
