"""查询历史持久化（SQLite）。

前端 docs/app.js 有「查询历史」面板，调用 /api/history，但此前后端没有实现。
本模块补上数据层：用 Python 标准库 sqlite3，无需额外依赖。

设计要点（面试可讲）：
  1. 建表用 CREATE TABLE IF NOT EXISTS，服务重启不丢数据、不重复建表。
  2. 所有 SQL 用 ? 占位符（参数化查询），不拼字符串 —— 防 SQL 注入。
  3. 建 (session_id, created_at DESC) 联合索引 —— 查询永远按会话过滤 + 按时间倒序，
     正好命中索引的最左前缀，避免全表扫描（对应八股的「索引」）。
  4. 每次操作都新建连接（with 自动关闭）—— FastAPI 的同步路由跑在线程池，
     sqlite3 连接默认不能跨线程，短连接是最省心的线程安全做法。
  5. WAL 模式 + busy_timeout —— 降低并发写时的 "database is locked" 概率。

数据库文件默认在 backend/data/trip_history.db，可用环境变量 TRIP_HISTORY_DB 覆盖（测试时用临时库）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "trip_history.db"
)


def _db_path() -> str:
    """每次读取环境变量，方便测试切到临时库，避免污染真实数据。"""
    return os.getenv("TRIP_HISTORY_DB", _DEFAULT_DB)


def _connect() -> sqlite3.Connection:
    """新建连接。with 语句会在结束时自动 commit 并关闭。"""
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row  # 让查询结果能按列名取值，如 row["destination"]
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """建表 + 建索引。幂等：重复调用不会报错，也不会丢已有数据。"""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trip_history (
                id            TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL,
                destination   TEXT NOT NULL,
                days          INTEGER NOT NULL,
                preferences   TEXT NOT NULL DEFAULT '[]',
                ok            INTEGER NOT NULL DEFAULT 1,
                error         TEXT NOT NULL DEFAULT '',
                n_attractions INTEGER NOT NULL DEFAULT 0,
                budget_total  REAL NOT NULL DEFAULT 0,
                elapsed_s     REAL NOT NULL DEFAULT 0,
                arch          TEXT NOT NULL DEFAULT '',
                plan_json     TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_history_session_time
                ON trip_history(session_id, created_at DESC);
            """
        )


def save(record: dict[str, Any]) -> None:
    """插入一条历史。preferences 是 list，存之前转成 JSON 字符串。"""
    sql = """
        INSERT INTO trip_history
            (id, session_id, destination, days, preferences, ok, error,
             n_attractions, budget_total, elapsed_s, arch, plan_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    row = dict(record)
    row["preferences"] = json.dumps(row.get("preferences") or [], ensure_ascii=False)
    params = (
        row["id"], row["session_id"], row["destination"], row["days"],
        row["preferences"], int(row.get("ok", 1)), row.get("error", ""),
        row.get("n_attractions", 0), row.get("budget_total", 0),
        row.get("elapsed_s", 0), row.get("arch", ""),
        row.get("plan_json", ""), row["created_at"],
    )
    with _connect() as conn:
        conn.execute(sql, params)


def list_by_session(session_id: str, limit: int = 12) -> list[dict]:
    """按会话查历史列表，按时间倒序。命中 (session_id, created_at) 索引。"""
    sql = """
        SELECT id, destination, days, preferences, ok, error,
               n_attractions, budget_total, elapsed_s, arch, created_at
        FROM trip_history
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """
    with _connect() as conn:
        rows = conn.execute(sql, (session_id, limit)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["preferences"] = json.loads(d["preferences"] or "[]")
        d["ok"] = bool(d["ok"])
        items.append(d)
    return items


def get_one(record_id: str, session_id: str) -> dict | None:
    """取单条完整记录（含 plan_json，供前端回放）。带 session_id 防止越权访问。"""
    sql = "SELECT * FROM trip_history WHERE id = ? AND session_id = ?"
    with _connect() as conn:
        row = conn.execute(sql, (record_id, session_id)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["preferences"] = json.loads(d["preferences"] or "[]")
    d["ok"] = bool(d["ok"])
    return d


def delete_one(record_id: str, session_id: str) -> bool:
    """删除一条。返回是否真的删到了（rowcount > 0）。"""
    sql = "DELETE FROM trip_history WHERE id = ? AND session_id = ?"
    with _connect() as conn:
        cur = conn.execute(sql, (record_id, session_id))
        return cur.rowcount > 0


def count_all() -> int:
    """总条数，测试用。"""
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM trip_history").fetchone()[0]
