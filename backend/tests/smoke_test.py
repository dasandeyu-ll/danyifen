"""端到端冒烟测试：不用真起服务，直接打 FastAPI 应用。

覆盖的是一条完整业务链路：
    灌数据 → 列表 → 分析 → 人工确认 → 状态机推进 → 看板统计 → 跑评测
这条链路能过，说明"识别 → 分拣"是通的。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ANALYZER_MODE", "rule")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [OK]   {name} {detail}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def test_basics(client: TestClient) -> None:
    print("\n--- 1. 健康检查 & 元信息 ---")
    health = client.get("/api/health").json()
    check("health ok", health["status"] == "ok")
    meta = client.get("/api/meta").json()
    check("meta 下发意图词典", len(meta["intents"]) == 9, f"共 {len(meta['intents'])} 个意图")
    check("meta 下发状态机图", "verify" in meta["graph_mermaid"])


def test_seed_and_list(client: TestClient) -> None:
    print("\n--- 2. 灌入演示数据并自动分析 ---")
    seeded = client.post("/api/seed").json()
    check("灌入演示工单", seeded["created"] == 14, f"{seeded['created']} 条")

    tickets = client.get("/api/tickets").json()
    check("工单列表非空", len(tickets) == 14, f"{len(tickets)} 条")
    check("全部工单已分析出目标", all(t["goals"] for t in tickets))

    multi = [t for t in tickets if len(t["goals"]) > 1]
    check("单句多目标拆分生效", len(multi) >= 3, f"{len(multi)} 条工单被拆成多个目标")

    asking = [t for t in tickets if t["status"] == "asking"]
    check("存在缺槽位追问的工单", len(asking) >= 1, f"{len(asking)} 条")
    if asking:
        questions = asking[0]["goals"][0]["questions"]
        check("追问话术已生成", bool(questions), questions[0] if questions else "")

    review = [t for t in tickets if t["need_human"]]
    check("存在需人工确认的工单", len(review) >= 1, f"{len(review)} 条")


def test_analyze(client: TestClient) -> None:
    print("\n--- 3. 两阶段分析（不落库试算） ---")
    probe = client.post(
        "/api/tickets/analyze",
        json={"text": "订单号 SF20240715999，我买的显示器有质量问题想退货，另外顺便问下发票怎么开。"},
    ).json()
    check("试算返回目标", len(probe["goals"]) >= 2, f"拆出 {len(probe['goals'])} 个目标")
    check(
        "试算未落库",
        all(t["id"] != probe["id"] for t in client.get("/api/tickets").json()),
    )
    nodes = [s["node"] for s in probe["trace"]]
    check("状态机轨迹完整", nodes[:4] == ["split", "classify", "extract", "verify"], str(nodes))


def test_confirm_flow(client: TestClient) -> None:
    print("\n--- 4. 人工确认 → 状态机推进 ---")
    target = next((t for t in client.get("/api/tickets").json() if t["need_human"]), None)
    if not target:
        check("找到需人工确认的工单", False)
        return

    goal = next(g for g in target["goals"] if g["need_human"])
    resp = client.post(
        "/api/tickets/confirm",
        json={
            "ticket_id": target["id"],
            "goal_id": goal["id"],
            "action": "correct",
            "intent": goal["intent"],
            "slots": {"订单号": "SF20240715001"},
        },
    )
    check("确认接口返回 200", resp.status_code == 200, resp.text[:120])
    updated = resp.json()
    after = next(g for g in updated["goals"] if g["id"] == goal["id"])
    check("确认后不再需要人工", after["need_human"] is False, f"{goal['status']} -> {after['status']}")
    check("人工填写槽位被采纳", any(s["value"] for s in after["slots"]))
    check("人工修正计入统计", after["human_action"] == "correct")

    done = client.post(
        "/api/tickets/advance",
        json={"ticket_id": target["id"], "goal_id": goal["id"], "target": "done"},
    )
    check("ready → done 迁移成功", done.status_code == 200, done.text[:120])
    if done.status_code == 200:
        final = next(g for g in done.json()["goals"] if g["id"] == goal["id"])
        check("目标状态为 done", final["status"] == "done")


def test_illegal_transition(client: TestClient) -> None:
    print("\n--- 5. 非法迁移必须被拒绝 ---")
    for ticket in client.get("/api/tickets").json():
        goal = next((g for g in ticket["goals"] if g["status"] == "pending"), None)
        if goal:
            resp = client.post(
                "/api/tickets/advance",
                json={"ticket_id": ticket["id"], "goal_id": goal["id"], "target": "done"},
            )
            check("pending → done 被拒绝", resp.status_code == 409, f"HTTP {resp.status_code}")
            return
    check("找到 pending 目标用于测试非法迁移", False)


def test_stats(client: TestClient) -> None:
    print("\n--- 6. 一键分拣 & 看板统计 ---")
    bulk = client.post("/api/tickets/dispatch-all").json()
    check("批量分拣接口可用", "dispatched_goals" in bulk, f"分拣 {bulk['dispatched_goals']} 个目标")

    stats = client.get("/api/stats").json()
    check("统计有总工单数", stats["total_tickets"] == 14, str(stats["total_tickets"]))
    check("意图分布非空", len(stats["intent_distribution"]) >= 5, str(len(stats["intent_distribution"])))
    check("意图带色板", all(b["color"].startswith("#") for b in stats["intent_distribution"]))
    check("状态分布非空", len(stats["status_distribution"]) >= 1)
    check("准确率报告可算", 0.0 <= stats["accuracy"]["overall"] <= 1.0, str(stats["accuracy"]["overall"]))
    check("缺槽位榜单可算", isinstance(stats["top_slots_missing"], list))


def test_eval_and_delete(client: TestClient) -> None:
    print("\n--- 7. 评测接口 & 删除 ---")
    report = client.post("/api/eval/run", json={"engine": "rule", "limit": 60}).json()
    check("评测能跑通", report["total_samples"] == 60, f"{report['total_samples']} 条")
    check("意图准确率在合理区间", 0.5 <= report["intent_accuracy"] <= 1.0, str(report["intent_accuracy"]))
    check("槽位 F1 在合理区间", 0.5 <= report["slot_f1"] <= 1.0, str(report["slot_f1"]))

    victim = client.get("/api/tickets").json()[-1]["id"]
    check("删除返回 200", client.delete(f"/api/tickets/{victim}").status_code == 200)
    check("删除后查不到", client.get(f"/api/tickets/{victim}").status_code == 404)


def main() -> int:
    with TestClient(app) as client:
        test_basics(client)
        test_seed_and_list(client)
        test_analyze(client)
        test_confirm_flow(client)
        test_illegal_transition(client)
        test_stats(client)
        test_eval_and_delete(client)

    print("\n" + "=" * 58)
    print(f"  通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for name in FAILED:
        print(f"    x {name}")
    print("=" * 58 + "\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
