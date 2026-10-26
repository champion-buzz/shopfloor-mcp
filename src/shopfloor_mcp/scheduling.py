"""Pure scheduling logic, kept separate from the MCP layer so it is easy to test."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

WORKDAY_START_HOUR = 8


class SchedulingError(ValueError):
    """Raised when a request is invalid or would create a conflict."""


@dataclass(frozen=True)
class Block:
    work_order_id: int
    resource_id: int
    start: datetime
    end: datetime


def parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise SchedulingError(f"'{value}' is not an ISO datetime like 2026-11-02T08:00") from exc


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def conflicts(conn: sqlite3.Connection, resource_id: int, start: datetime, end: datetime) -> list[dict]:
    rows = conn.execute(
        "SELECT b.*, w.title FROM schedule_blocks b JOIN work_orders w ON w.id = b.work_order_id "
        "WHERE b.resource_id = ?",
        (resource_id,),
    ).fetchall()
    return [
        {"block_id": r["id"], "work_order_id": r["work_order_id"], "title": r["title"], "start": r["start"], "end": r["end"]}
        for r in rows
        if overlaps(start, end, datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]))
    ]


def _require(conn: sqlite3.Connection, table: str, row_id: int) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()  # table name is internal
    if row is None:
        raise SchedulingError(f"No {table[:-1].replace('_', ' ')} with id {row_id}")
    return row


def schedule(conn: sqlite3.Connection, work_order_id: int, resource_id: int, start: datetime, hours: float) -> Block:
    """Book a resource for a work order. Rejects overlaps instead of silently double-booking."""
    if hours <= 0 or hours > 24:
        raise SchedulingError("hours must be between 0 and 24")
    wo = _require(conn, "work_orders", work_order_id)
    _require(conn, "resources", resource_id)
    if wo["status"] == "done":
        raise SchedulingError(f"Work order {work_order_id} is already done")
    end = start + timedelta(hours=hours)
    clash = conflicts(conn, resource_id, start, end)
    if clash:
        raise SchedulingError(f"Resource {resource_id} is already booked: {clash}")
    conn.execute(
        "INSERT INTO schedule_blocks (work_order_id, resource_id, start, end) VALUES (?, ?, ?, ?)",
        (work_order_id, resource_id, start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")),
    )
    conn.execute("UPDATE work_orders SET status = 'scheduled' WHERE id = ? AND status = 'open'", (work_order_id,))
    conn.commit()
    return Block(work_order_id, resource_id, start, end)


def next_free_slot(conn: sqlite3.Connection, resource_id: int, hours: float, not_before: datetime) -> datetime:
    """First start time on/after not_before where the resource is free for `hours` within one workday."""
    res = _require(conn, "resources", resource_id)
    day_len = timedelta(hours=res["hours_per_day"])
    if timedelta(hours=hours) > day_len:
        raise SchedulingError(f"{hours}h does not fit in one {res['hours_per_day']}h workday; split the job")
    candidate = not_before
    for _ in range(24 * 60):  # bounded search, 30-minute steps
        day_open = candidate.replace(hour=WORKDAY_START_HOUR, minute=0, second=0, microsecond=0)
        if candidate < day_open:
            candidate = day_open
        if candidate.weekday() >= 5 or candidate + timedelta(hours=hours) > day_open + day_len:
            candidate = (day_open + timedelta(days=1))
            continue
        clash = conflicts(conn, resource_id, candidate, candidate + timedelta(hours=hours))
        if not clash:
            return candidate
        candidate = max(datetime.fromisoformat(c["end"]) for c in clash)
    raise SchedulingError("No free slot found in the search window")


def utilization(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """Booked hours vs. available hours per resource for weekdays in [start, end]."""
    workdays = sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)
    out = []
    for r in conn.execute("SELECT * FROM resources ORDER BY id"):
        booked = 0.0
        for b in conn.execute("SELECT start, end FROM schedule_blocks WHERE resource_id = ?", (r["id"],)):
            s, e = datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])
            if s.date() >= start and e.date() <= end:
                booked += (e - s).total_seconds() / 3600
        available = workdays * r["hours_per_day"]
        out.append({
            "resource": r["name"],
            "booked_hours": round(booked, 2),
            "available_hours": available,
            "utilization_pct": round(100 * booked / available, 1) if available else 0.0,
        })
    return out


def late_risks(conn: sqlite3.Connection, today: date) -> list[dict]:
    """Work orders whose booked hours end after the due date, or that are unscheduled and due within 3 days."""
    risks = []
    for wo in conn.execute("SELECT * FROM work_orders WHERE status != 'done' ORDER BY due_date"):
        due = date.fromisoformat(wo["due_date"])
        blocks = conn.execute("SELECT start, end FROM schedule_blocks WHERE work_order_id = ?", (wo["id"],)).fetchall()
        booked = sum((datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).total_seconds() / 3600 for b in blocks)
        last_end = max((datetime.fromisoformat(b["end"]) for b in blocks), default=None)
        reason = None
        if last_end and last_end.date() > due:
            reason = f"last block ends {last_end.date()} after due date {due}"
        elif booked < wo["est_hours"] and (due - today).days <= 3:
            reason = f"only {booked:g}/{wo['est_hours']:g}h booked and due in {(due - today).days} day(s)"
        if reason:
            risks.append({"work_order_id": wo["id"], "title": wo["title"], "customer": wo["customer"],
                          "due_date": wo["due_date"], "priority": wo["priority"], "reason": reason})
    return risks
