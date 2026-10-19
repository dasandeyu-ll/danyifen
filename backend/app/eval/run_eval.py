"""评测执行器：意图准确率 / 槽位 F1 / 拆分准确率 / 转人工召回。

指标怎么算的，先说清楚（不然数字没法解释）
------------------------------------------
1. intent_accuracy（样本级）
   预测出的意图序列 == 标注的意图序列，才算这条样本对。
   多目标样本要求"拆对了 + 顺序也对"，所以这个指标同时惩罚了拆分错误。

2. slot_f1（微平均 micro-F1）
   把所有目标的槽位摊平成 (槽位名, 归一化值) 的集合，算 P/R/F1。
   * 只统计结构化槽位。像"咨询主题""建议主题""投诉事由"这种自由文本槽位
     没有唯一标准答案，纳入计算只会让指标失真，所以显式排除（FREE_TEXT_SLOTS）。
   * 值做了归一化（去空白、订单号统一大写），避免大小写这种无意义扣分。

3. escalation_recall（转人工召回）
   在 20 条"模糊表达"样本上，系统有没有把 need_human 标出来。
   这是"人机协同"系统最关键的安全指标 —— 宁可多转人工，不可错分。

4. ask_recall（追问召回）
   在 10 条"缺必需槽位"样本上，系统有没有进入 asking 状态并生成追问话术。

5. false_escalation_rate（误转人工率）
   在 130 条清晰样本上被错误转人工的比例。它和 escalation_recall 是一对
   互相制约的指标 —— 只报其中一个都是在耍流氓。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..schemas import GoalStatus
from .build_dataset import build

DATASET = Path(__file__).with_name("dataset.jsonl")

# 自由文本槽位：没有唯一标准答案，不纳入 F1 计算
FREE_TEXT_SLOTS = {"咨询主题", "建议主题", "投诉事由"}


def load_dataset() -> list[dict[str, Any]]:
    if not DATASET.exists():
        records = build()
        with DATASET.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return records
    with DATASET.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def norm_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).upper()


def slot_pairs(goals: list[dict[str, Any]]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for goal in goals:
        for name, value in (goal.get("slots") or {}).items():
            if name in FREE_TEXT_SLOTS or value in (None, ""):
                continue
            pairs.add((name, norm_value(value)))
    return pairs


async def _evaluate(engine: str, limit: int | None) -> dict[str, Any]:
    from ..agent.graph import run_agent

    records = load_dataset()
    if limit:
        records = records[:limit]

    tp = fp = fn = 0
    intent_hits = 0
    intent_total = 0
    seg_hits = 0
    seg_total = 0
    escalated_on_hard = 0
    hard_total = 0
    asked_on_missing = 0
    missing_total = 0
    false_escalation = 0
    clean_total = 0
    errors: list[dict[str, Any]] = []
    by_intent: dict[str, dict[str, int]] = {}
    by_tag: dict[str, dict[str, int]] = {}

    def tag_bump(tag: str, key: str, n: int = 1) -> None:
        by_tag.setdefault(
            tag,
            {"samples": 0, "intent_correct": 0, "slot_tp": 0, "slot_fp": 0, "slot_fn": 0},
        )
        by_tag[tag][key] += n

    for record in records:
        expected = record["goals"]
        goals, _trace, _engine = await run_agent(record["text"], engine)
        predicted = [
            {"intent": g.intent.value, "slots": {s.name: s.value for s in g.slots if s.value}}
            for g in goals
        ]

        def bump(intent: str, key: str) -> None:
            by_intent.setdefault(intent, {"total": 0, "correct": 0})
            by_intent[intent][key] += 1

        # ---- 模糊样本：只关心有没有转人工 ---- #
        if record["tag"] == "hard":
            hard_total += 1
            flagged = any(
                g.need_human or g.intent.value == "其他" or g.status != GoalStatus.READY
                for g in goals
            )
            escalated_on_hard += int(flagged)
            continue

        # ---- 缺槽位样本：关心有没有追问 ---- #
        if record["tag"] == "missing_slot":
            missing_total += 1
            asked_on_missing += int(
                any(g.status == GoalStatus.ASKING and g.questions for g in goals)
            )

        # ---- 拆分准确率 ---- #
        seg_total += 1
        seg_hits += int(len(predicted) == len(expected))

        # ---- 意图准确率（样本级） ---- #
        intent_total += 1
        exp_intents = [g["intent"] for g in expected]
        pred_intents = [g["intent"] for g in predicted]
        ok = exp_intents == pred_intents
        intent_hits += int(ok)
        for intent in set(exp_intents):
            bump(intent, "total")
        if ok:
            for intent in set(exp_intents):
                bump(intent, "correct")

        # ---- 误转人工率（只在清晰样本上统计） ---- #
        if record["tag"] in ("single", "multi", "paraphrase"):
            clean_total += 1
            false_escalation += int(any(g.need_human for g in goals))

        # ---- 槽位微平均 P/R/F1 ---- #
        gold = slot_pairs(expected)
        got = slot_pairs(predicted)
        tp += len(gold & got)
        fp += len(got - gold)
        fn += len(gold - got)

        tag_bump(record["tag"], "samples")
        tag_bump(record["tag"], "intent_correct", int(ok))
        tag_bump(record["tag"], "slot_tp", len(gold & got))
        tag_bump(record["tag"], "slot_fp", len(got - gold))
        tag_bump(record["tag"], "slot_fn", len(gold - got))

        if not ok and len(errors) < 30:
            errors.append(
                {
                    "id": record["id"],
                    "tag": record["tag"],
                    "text": record["text"],
                    "expected": exp_intents,
                    "predicted": pred_intents,
                    "predicted_confidence": [g.confidence for g in goals],
                }
            )

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "engine": engine,
        "total_samples": len(records),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "intent_accuracy": round(intent_hits / intent_total, 4) if intent_total else 0.0,
        "intent_correct": intent_hits,
        "intent_total": intent_total,
        "segmentation_accuracy": round(seg_hits / seg_total, 4) if seg_total else 0.0,
        "slot_precision": round(precision, 4),
        "slot_recall": round(recall, 4),
        "slot_f1": round(f1, 4),
        "slot_tp": tp,
        "slot_fp": fp,
        "slot_fn": fn,
        "escalation_recall": round(escalated_on_hard / hard_total, 4) if hard_total else 0.0,
        "escalation_total": hard_total,
        "ask_recall": round(asked_on_missing / missing_total, 4) if missing_total else 0.0,
        "ask_total": missing_total,
        "false_escalation_rate": round(false_escalation / clean_total, 4) if clean_total else 0.0,
        "by_intent": [
            {
                "intent": intent,
                "total": v["total"],
                "correct": v["correct"],
                "accuracy": round(v["correct"] / v["total"], 4) if v["total"] else 0.0,
            }
            for intent, v in sorted(by_intent.items(), key=lambda kv: -kv[1]["total"])
        ],
        "by_tag": [
            {
                "tag": tag,
                "samples": v["samples"],
                "intent_accuracy": round(v["intent_correct"] / v["samples"], 4) if v["samples"] else None,
                "slot_f1": round(
                    2 * v["slot_tp"] / (2 * v["slot_tp"] + v["slot_fp"] + v["slot_fn"]), 4
                )
                if (v["slot_tp"] or v["slot_fp"] or v["slot_fn"])
                else None,
            }
            for tag, v in sorted(by_tag.items(), key=lambda kv: -kv[1]["samples"])
        ],
        "errors": errors,
    }


def run_evaluation(engine: str = "rule", limit: int | None = None) -> dict[str, Any]:
    """同步入口（FastAPI 的同步路由会在线程池里跑，asyncio.run 是安全的）。"""
    return asyncio.run(_evaluate(engine, limit))


def main() -> None:  # pragma: no cover
    import sys

    engine = sys.argv[1] if len(sys.argv) > 1 else "rule"
    report = run_evaluation(engine=engine)

    print()
    print("=" * 62)
    print(f"  单易分 · 评测报告   引擎={report['engine']}   样本={report['total_samples']}")
    print("=" * 62)
    rows = [
        ("意图准确率（样本级）", "intent_accuracy", "intent_correct", "intent_total"),
        ("单句多目标拆分准确率", "segmentation_accuracy", None, None),
        ("槽位精确率 P", "slot_precision", None, None),
        ("槽位召回率 R", "slot_recall", None, None),
        ("槽位 F1", "slot_f1", None, None),
        ("转人工召回（模糊样本）", "escalation_recall", None, "escalation_total"),
        ("追问召回（缺槽位样本）", "ask_recall", None, "ask_total"),
        ("误转人工率（清晰样本）", "false_escalation_rate", None, None),
    ]
    for label, key, num, den in rows:
        value = report[key]
        extra = ""
        if num and den:
            extra = f"   ({report[num]}/{report[den]})"
        print(f"  {label:<24s} {value:>8.4f}{extra}")

    print("-" * 62)
    print("  分层明细（调优集 vs 留出集）")
    for item in report["by_tag"]:
        acc = f"{item['intent_accuracy']:.4f}" if item["intent_accuracy"] is not None else "  --  "
        f1 = f"{item['slot_f1']:.4f}" if item["slot_f1"] is not None else "  --  "
        print(f"  {item['tag']:<14s} n={item['samples']:<4d} 意图准确率={acc}  槽位F1={f1}")

    print("-" * 62)
    print("  分意图明细")
    for item in report["by_intent"]:
        bar = "█" * int(item["accuracy"] * 20)
        print(
            f"  {item['intent']:<8s} {item['correct']:>3d}/{item['total']:<3d} "
            f"{item['accuracy']:.3f} {bar}"
        )

    if report["errors"]:
        print("-" * 62)
        print(f"  错例（最多展示 12 条，总计 {len(report['errors'])}）")
        for err in report["errors"][:12]:
            print(f"  [{err['id']}/{err['tag']}] {err['text'][:56]}")
            print(f"        期望 {err['expected']}  →  预测 {err['predicted']}  {err['predicted_confidence']}")
    print("=" * 62)
    print()


if __name__ == "__main__":  # pragma: no cover
    main()
