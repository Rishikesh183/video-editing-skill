"""Token log for every planner call (Claude Code or Codex), in SQLite, optionally mirrored to Firebase."""

import logging
import sqlite3
from contextlib import closing
from typing import Any

from . import config
from .models import now

log = logging.getLogger(__name__)

_SCHEMA = """
create table if not exists calls (
  id integer primary key,
  ts text not null,
  job_id text,
  step text not null,
  model text,
  input_tokens integer default 0,
  output_tokens integer default 0,
  thinking_tokens integer default 0,
  cache_creation_tokens integer default 0,
  cache_read_tokens integer default 0,
  cost_usd real default 0,
  duration_ms integer default 0,
  provider text default 'claude'
)
"""


def _conn() -> sqlite3.Connection:
    config.USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.USAGE_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    columns = {r[1] for r in conn.execute("pragma table_info(calls)")}
    if "provider" not in columns:  # databases made before Codex support
        conn.execute("alter table calls add column provider text default 'claude'")
    return conn


def record(job_id: str | None, step: str, result: dict[str, Any], provider: str = "claude") -> dict[str, Any]:
    """Store the usage block of a planner call (`claude -p --output-format json` shape)."""
    usage = result.get("usage") or {}
    models = result.get("modelUsage") or {}
    row = {
        "ts": now().replace("T", " ")[:19],  # SQLite datetime() format, UTC
        "job_id": job_id,
        "step": step,
        "model": ",".join(models) or None,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "thinking_tokens": (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cost_usd": result.get("total_cost_usd", 0.0),
        "duration_ms": result.get("duration_ms", 0),
        "provider": provider,
    }
    with closing(_conn()) as conn, conn:
        conn.execute(f"insert into calls ({','.join(row)}) values ({','.join('?' * len(row))})", list(row.values()))
    _mirror(row)
    return row


_firebase_db: Any = None


def _mirror(row: dict[str, Any]) -> None:
    """Copy a usage row to Firestore when HF_FIREBASE_CREDENTIALS points at a service-account file.
    Never fails the planner call: the SQLite log is the source of truth."""
    global _firebase_db
    if not config.FIREBASE_CREDENTIALS:
        return
    try:
        if _firebase_db is None:
            import firebase_admin  # optional: uv sync --extra firebase
            from firebase_admin import credentials, firestore

            if not firebase_admin._apps:
                firebase_admin.initialize_app(credentials.Certificate(config.FIREBASE_CREDENTIALS))
            _firebase_db = firestore.client()
        _firebase_db.collection(config.FIREBASE_COLLECTION).add(row)
    except Exception as e:  # noqa: BLE001
        log.warning("firebase mirror skipped: %s", e)


_TOTALS = """
  count(*) calls, coalesce(sum(input_tokens), 0) input_tokens, coalesce(sum(output_tokens), 0) output_tokens,
  coalesce(sum(cache_creation_tokens), 0) cache_creation_tokens, coalesce(sum(cache_read_tokens), 0) cache_read_tokens,
  round(coalesce(sum(cost_usd), 0), 4) cost_usd
"""


def summary(job_id: str | None = None) -> dict[str, Any]:
    where, args = ("where job_id = ?", [job_id]) if job_id else ("", [])
    with closing(_conn()) as conn:
        total = dict(conn.execute(f"select {_TOTALS} from calls {where}", args).fetchone())
        by_step = [dict(r) for r in conn.execute(f"select step, {_TOTALS} from calls {where} group by step", args)]
        week = dict(conn.execute(f"select {_TOTALS} from calls where ts >= datetime('now', '-7 days')").fetchone())
        recent = [dict(r) for r in conn.execute(f"select * from calls {where} order by id desc limit 50", args)]
    return {"total": total, "by_step": by_step, "last_7_days": week, "recent": recent}


def dashboard() -> dict[str, Any]:
    """Everything the dashboard page shows: windows per provider, per-job totals and a daily series.
    Claude's limits are a rolling 5-hour session and a weekly cap, so those are the two windows."""
    with closing(_conn()) as conn:
        def window(since: str) -> dict[str, dict[str, Any]]:
            rows = conn.execute(f"select provider, {_TOTALS} from calls where step = 'plan' and ts >= datetime('now', ?) "
                                "group by provider", [since])
            return {r["provider"]: dict(r) for r in rows}

        jobs = [dict(r) for r in conn.execute(
            f"select job_id, min(ts) first_ts, max(ts) last_ts, group_concat(distinct provider) providers, "
            f"sum(step = 'plan') plans, sum(step = 'plan (cached)') cached, {_TOTALS} "
            "from calls where job_id is not null group by job_id order by max(ts) desc limit 200")]
        daily = [dict(r) for r in conn.execute(
            "select date(ts) day, provider, count(*) calls, coalesce(sum(input_tokens + cache_creation_tokens), 0) input_tokens, "
            "coalesce(sum(output_tokens), 0) output_tokens, round(coalesce(sum(cost_usd), 0), 4) cost_usd from calls "
            "where step = 'plan' and ts >= datetime('now', '-30 days') group by day, provider order by day")]
        all_time = {r["provider"]: dict(r) for r in conn.execute(f"select provider, {_TOTALS} from calls where step = 'plan' group by provider")}
        session, week = window("-5 hours"), window("-7 days")
    return {"session_5h": session, "week": week, "all_time": all_time, "jobs": jobs, "daily": daily}
