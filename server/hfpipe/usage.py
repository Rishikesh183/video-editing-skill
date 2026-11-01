"""Token log for every planner call (Claude Code or Codex), in SQLite, optionally mirrored to Firebase."""

import json
import logging
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
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


_LIMITS_SCHEMA = """
create table if not exists limit_spend (
  id integer primary key,
  ts text not null,
  job_id text,
  step text not null,
  session_before real,
  session_after real,
  week_before real,
  week_after real,
  session_resets integer,
  week_resets integer
)
"""


def _conn() -> sqlite3.Connection:
    config.USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.USAGE_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.execute(_LIMITS_SCHEMA)
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


def reading() -> dict[str, Any] | None:
    """How much of the Claude plan limits is used right now, as Claude Code itself reports it (0-100).
    It needs a call to the service, so this sends the smallest one possible. None if it cannot be read."""
    cmd = [config.claude_executable(), "-p", "ok", "--model", "haiku", "--tools", "", "--output-format", "stream-json", "--verbose",
           "--system-prompt", "Reply ok.", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--setting-sources", ""]
    try:
        with tempfile.TemporaryDirectory(prefix="hf-limits-") as cwd:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=90)
        return parse_reading(proc.stdout)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        log.warning("could not read plan limits: %s", e)
        return None


def parse_reading(stream: str) -> dict[str, Any] | None:
    found = None
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "rate_limit_event":
            found = (event.get("rate_limit_info") or {}).get("unifiedWindows") or found
    if not found or "five_hour" not in found or "seven_day" not in found:
        return None
    session, week = found["five_hour"], found["seven_day"]
    return {"session": round(session["utilization"] * 100, 1), "week": round(week["utilization"] * 100, 1),
            "session_resets": session.get("resetsAt"), "week_resets": week.get("resetsAt")}


_metering = False


@contextmanager
def metered(job_id: str, step: str) -> Iterator[None]:
    """Reads the limits around a job step, so an edit can say what it really cost. Anything else using the same Claude
    account at that moment counts too. A step inside a metered step is part of it."""
    global _metering
    if _metering:
        yield
        return
    _metering = True
    before = reading()
    try:
        yield
    finally:
        _metering = False
        after = reading()
        if before and after:
            with closing(_conn()) as conn, conn:
                conn.execute("insert into limit_spend (ts, job_id, step, session_before, session_after, week_before, week_after, "
                             "session_resets, week_resets) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             [now().replace("T", " ")[:19], job_id, step, before["session"], after["session"], before["week"], after["week"],
                              after["session_resets"], after["week_resets"]])


def _spent(before: float, after: float) -> float:
    """Points of a limit used. A lower second reading means the window reset, and all of it is new."""
    return after if after < before else after - before


def job_limits(job_id: str) -> dict[str, Any] | None:
    """What an edit took out of the plan limits so far, and where they stand after it."""
    with closing(_conn()) as conn:
        rows = [dict(r) for r in conn.execute("select * from limit_spend where job_id = ? order by id", [job_id])]
    if not rows:
        return None
    last = rows[-1]
    return {"session_used": round(sum(_spent(r["session_before"], r["session_after"]) for r in rows), 1),
            "week_used": round(sum(_spent(r["week_before"], r["week_after"]) for r in rows), 1),
            "session_now": last["session_after"], "week_now": last["week_after"], "session_resets": last["session_resets"]}


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
            rows = conn.execute(f"select provider, {_TOTALS} from calls where step != 'plan (cached)' and ts >= datetime('now', ?) "
                                "group by provider", [since])
            return {r["provider"]: dict(r) for r in rows}

        jobs = [dict(r) for r in conn.execute(
            f"select job_id, min(ts) first_ts, max(ts) last_ts, group_concat(distinct provider) providers, "
            f"sum(step in ('plan', 'voice plan')) plans, sum(step = 'plan (cached)') cached, {_TOTALS} "
            "from calls where job_id is not null group by job_id order by max(ts) desc limit 200")]
        daily = [dict(r) for r in conn.execute(
            "select date(ts) day, provider, count(*) calls, coalesce(sum(input_tokens + cache_creation_tokens), 0) input_tokens, "
            "coalesce(sum(output_tokens), 0) output_tokens, round(coalesce(sum(cost_usd), 0), 4) cost_usd from calls "
            "where step != 'plan (cached)' and ts >= datetime('now', '-30 days') group by day, provider order by day")]
        all_time = {r["provider"]: dict(r) for r in conn.execute(f"select provider, {_TOTALS} from calls where step != 'plan (cached)' group by provider")}
        session, week = window("-5 hours"), window("-7 days")
        latest = conn.execute("select * from limit_spend order by id desc limit 1").fetchone()
    for job in jobs:
        job["limits"] = job_limits(job["job_id"])
    now_limits = {"session": latest["session_after"], "week": latest["week_after"], "session_resets": latest["session_resets"],
                  "week_resets": latest["week_resets"], "ts": latest["ts"]} if latest else None
    return {"session_5h": session, "week": week, "all_time": all_time, "jobs": jobs, "daily": daily, "limits_now": now_limits}
