"""SQLite 持久层：只做两件事 —— 存取 Ticket 聚合、算看板统计。

设计取舍
--------
* 不引 ORM。本项目的查询全是"按 id 取聚合 + 几个 group by"，
  ORM 的关系映射能力用不上，反而让 SQL 变得不可见、难调优。
* Goal 存 JSON 快照（payload），同时把 intent / status / confidence
  这几个"要 group by 的字段"冗余成真列。这是读多写少场景的标准做法：
  查询走索引列，展示走 JSON，两不耽误。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import get_settings
from .schemas import (
    AccuracyReport,
    Goal,
    GoalStatus,
    INTENT_COLOR,
    Intent,
    IntentBucket,
    StatusBucket,
    StatsResponse,
    Ticket,
    TicketStatus,
    TraceStep,
    now_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id           TEXT PRIMARY KEY,
    customer     TEXT NOT NULL,
    channel      TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    created_date TEXT NOT NULL,
    status       TEXT NOT NULL,
    priority     TEXT NOT NULL DEFAULT '中',
    engine       TEXT NOT NULL DEFAULT 'rule',
    trace        TEXT NOT NULL DEFAULT '[]',
    analyzed_at  TEXT,
    ready_at     TEXT,
    done_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_date   ON tickets(created_date);

CREATE TABLE IF NOT EXISTS goals (
    id            TEXT PRIMARY KEY,
    ticket_id     TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    intent        TEXT NOT NULL,
    status        TEXT NOT NULL,
    confidence    REAL NOT NULL,
    need_human    INTEGER NOT NULL DEFAULT 0,
    human_action  TEXT,
    payload       TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goals_ticket ON goals(ticket_id);
CREATE INDEX IF NOT EXISTS idx_goals_intent ON goals(intent);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    payload   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
"""


def _db_file() -> Path:
    path = Path(get_settings().db_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_file(), check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# 极简迁移：只处理"加列"这一种最常见的需求。
# 换成 Alembic 对这个项目来说过重了，但完全不处理又会让老库直接崩掉 ——
# 所以保留这张"差集表"，新增列时把 SQL 往里加一行就行。
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("tickets", "trace", "TEXT NOT NULL DEFAULT '[]'"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            logger.info("已迁移：%s 新增列 %s", table, column)


def _goal_payload(g: Goal) -> dict[str, Any]:
    """只序列化真实字段，不把 computed_field 也塞进 JSON —— 保持存储干净。

    注意必须用 mode="json"：否则 SlotValue / Intent 这些富对象没法直接 json.dumps。
    """
    dumped = g.model_dump(mode="json")
    return {name: dumped[name] for name in Goal.model_fields}


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def _ticket_row(t: Ticket) -> tuple[Any, ...]:
    return (
        t.id,
        t.customer,
        t.channel,
        t.text,
        t.created_at,
        t.created_at[:10],
        t.status.value,
        t.priority,
        t.engine,
        json.dumps([s.model_dump(mode="json") for s in t.trace], ensure_ascii=False),
        t.analyzed_at,
        t.ready_at,
        t.done_at,
    )


def save_ticket(t: Ticket, *, log_event: str | None = None) -> Ticket:
    t = t.refreshed()  # 落库前强制同步一次聚合根状态
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tickets (id, customer, channel, text, created_at, created_date,
                                 status, priority, engine, trace, analyzed_at, ready_at, done_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                customer=excluded.customer, channel=excluded.channel, text=excluded.text,
                status=excluded.status, priority=excluded.priority, engine=excluded.engine,
                trace=excluded.trace,
                analyzed_at=excluded.analyzed_at, ready_at=excluded.ready_at, done_at=excluded.done_at
            """,
            _ticket_row(t),
        )
        conn.execute("DELETE FROM goals WHERE ticket_id = ?", (t.id,))
        conn.executemany(
            """
            INSERT INTO goals (id, ticket_id, intent, status, confidence,
                               need_human, human_action, payload, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    g.id,
                    t.id,
                    g.intent.value,
                    g.status.value,
                    g.confidence,
                    int(g.need_human),
                    g.human_action,
                    json.dumps(_goal_payload(g), ensure_ascii=False),
                    g.updated_at,
                )
                for g in t.goals
            ],
        )
        if log_event:
            conn.execute(
                "INSERT INTO events (ticket_id, ts, kind, payload) VALUES (?,?,?,?)",
                (t.id, now_iso(), log_event, json.dumps({"status": t.status.value}, ensure_ascii=False)),
            )
    return t


def delete_ticket(ticket_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        conn.execute("DELETE FROM goals WHERE ticket_id = ?", (ticket_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def _hydrate(conn: sqlite3.Connection, row: sqlite3.Row) -> Ticket:
    goals = [
        Goal(**json.loads(r["payload"]))
        for r in conn.execute(
            "SELECT payload FROM goals WHERE ticket_id = ? ORDER BY rowid", (row["id"],)
        )
    ]
    return Ticket(
        id=row["id"],
        customer=row["customer"],
        channel=row["channel"],
        text=row["text"],
        created_at=row["created_at"],
        status=TicketStatus(row["status"]),
        priority=row["priority"],
        engine=row["engine"],
        trace=[TraceStep(**s) for s in json.loads(row["trace"] or "[]")],
        analyzed_at=row["analyzed_at"],
        ready_at=row["ready_at"],
        done_at=row["done_at"],
        goals=goals,
    )


def get_ticket(ticket_id: str) -> Ticket | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _hydrate(conn, row) if row else None


def list_tickets(
    status: TicketStatus | None = None,
    keyword: str | None = None,
    limit: int = 100,
) -> list[Ticket]:
    sql = "SELECT * FROM tickets WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND status = ?"
        args.append(status.value)
    if keyword:
        sql += " AND (text LIKE ? OR customer LIKE ?)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [_hydrate(conn, r) for r in conn.execute(sql, args)]


def get_goal(ticket_id: str, goal_id: str) -> tuple[Ticket, Goal] | None:
    ticket = get_ticket(ticket_id)
    if not ticket:
        return None
    for g in ticket.goals:
        if g.id == goal_id:
            return ticket, g
    return None


def all_goals() -> list[tuple[str, Goal]]:
    with connect() as conn:
        return [
            (r["ticket_id"], Goal(**json.loads(r["payload"])))
            for r in conn.execute("SELECT ticket_id, payload FROM goals")
        ]


def list_events(ticket_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM events WHERE ticket_id = ? ORDER BY id DESC LIMIT ?",
                (ticket_id, limit),
            )
        ]


# --------------------------------------------------------------------------- #
# 看板统计
# --------------------------------------------------------------------------- #
def build_stats(engine: str = "rule") -> StatsResponse:
    today = datetime.now().strftime("%Y-%m-%d")
    with connect() as conn:
        total_tickets = conn.execute("SELECT COUNT(*) c FROM tickets").fetchone()["c"]
        today_tickets = conn.execute(
            "SELECT COUNT(*) c FROM tickets WHERE created_date = ?", (today,)
        ).fetchone()["c"]

        goal_rows = conn.execute(
            "SELECT intent, status, confidence, need_human, human_action FROM goals"
        ).fetchall()
        status_rows = conn.execute(
            "SELECT status, COUNT(*) c FROM tickets GROUP BY status"
        ).fetchall()
        missing_rows = conn.execute("SELECT payload FROM goals").fetchall()

    total_goals = len(goal_rows)
    avg_conf = round(sum(r["confidence"] for r in goal_rows) / total_goals, 4) if total_goals else 0.0
    needs_attention = sum(1 for r in goal_rows if r["need_human"])

    buckets: dict[str, list[float]] = {}
    for r in goal_rows:
        buckets.setdefault(r["intent"], []).append(r["confidence"])

    dist = [
        IntentBucket(
            intent=intent,
            count=len(scores),
            color=INTENT_COLOR.get(_as_intent(intent), "#94a3b8"),
            avg_confidence=round(sum(scores) / len(scores), 3),
        )
        for intent, scores in sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    ]
    dist.sort(key=lambda b: b.count, reverse=True)

    acc = AccuracyReport(
        auto_ready=sum(1 for r in goal_rows if not r["need_human"]),
        need_human=needs_attention,
        human_approved=sum(1 for r in goal_rows if r["human_action"] == "approve"),
        human_corrected=sum(1 for r in goal_rows if r["human_action"] == "correct"),
        human_rejected=sum(1 for r in goal_rows if r["human_action"] == "reject"),
    )

    missing_counter: dict[str, int] = {}
    for r in missing_rows:
        payload = json.loads(r["payload"])
        for s in payload.get("slots", []):
            if s.get("required") and not s.get("value"):
                missing_counter[s["name"]] = missing_counter.get(s["name"], 0) + 1

    return StatsResponse(
        total_tickets=total_tickets,
        total_goals=total_goals,
        today_tickets=today_tickets,
        avg_confidence=avg_conf,
        needs_attention=needs_attention,
        intent_distribution=dist,
        status_distribution=[
            StatusBucket(status=r["status"], count=r["c"])
            for r in sorted(status_rows, key=lambda r: _status_order(r["status"]))
        ],
        top_slots_missing=[
            {"slot": k, "count": v}
            for k, v in sorted(missing_counter.items(), key=lambda kv: -kv[1])[:6]
        ],
        accuracy=acc,
        engine=engine,
    )


def _as_intent(value: str) -> Intent:
    try:
        return Intent(value)
    except ValueError:
        return Intent.OTHER


def _status_order(value: str) -> int:
    order = {
        TicketStatus.PENDING.value: 0,
        TicketStatus.ASKING.value: 1,
        TicketStatus.READY.value: 2,
        TicketStatus.DONE.value: 3,
    }
    return order.get(value, 9)


def truncate_all() -> None:
    with connect() as conn:
        conn.executescript("DELETE FROM goals; DELETE FROM tickets; DELETE FROM events;")
