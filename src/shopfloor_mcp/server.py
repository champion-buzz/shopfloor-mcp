"""MCP server exposing shop-floor scheduling tools to AI assistants (Claude, Cursor, etc.)."""

from __future__ import annotations

import functools
import os
import sqlite3
from datetime import date, datetime
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import Annotated

from . import db, scheduling
from .scheduling import SchedulingError

READ_ONLY = os.environ.get("SHOPFLOOR_READ_ONLY", "0") == "1"
DB_PATH = os.environ.get("SHOPFLOOR_DB", "shopfloor.db")

INSTRUCTIONS = (
    "Tools for a small manufacturing shop. Look up work orders and resources before scheduling. "
    "Datetimes are ISO 8601 local time (e.g. 2026-11-02T08:00). Use find_next_slot instead of guessing a start time. "
    "Scheduling never overwrites existing bookings; on a conflict, report it to the user."
)

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)


def _clear_errors(fn):
    """Turn expected failures into ToolErrors so the model sees the reason and can recover."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (SchedulingError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def build_server(conn: sqlite3.Connection, read_only: bool = READ_ONLY) -> MCPServer:
    mcp = MCPServer(name="shopfloor", instructions=INSTRUCTIONS, version="0.1.0")

    def guard_write() -> None:
        if read_only:
            raise SchedulingError("Server is in read-only mode (SHOPFLOOR_READ_ONLY=1); no changes were made")

    @mcp.tool(annotations=READ)
    @_clear_errors
    def list_work_orders(
        status: Literal["open", "scheduled", "done", "any"] = "any",
        due_before: Annotated[str | None, Field(description="ISO date; only orders due on/before it")] = None,
    ) -> list[dict]:
        """List work orders, most urgent first (due date, then priority 1 = highest)."""
        sql, args = "SELECT * FROM work_orders WHERE 1=1", []
        if status != "any":
            sql += " AND status = ?"; args.append(status)
        if due_before:
            date.fromisoformat(due_before)
            sql += " AND due_date <= ?"; args.append(due_before)
        return [dict(r) for r in conn.execute(sql + " ORDER BY due_date, priority", args)]

    @mcp.tool(annotations=READ)
    @_clear_errors
    def get_work_order(work_order_id: Annotated[int, Field(gt=0)]) -> dict:
        """Get one work order with its scheduled blocks."""
        wo = conn.execute("SELECT * FROM work_orders WHERE id = ?", (work_order_id,)).fetchone()
        if wo is None:
            raise SchedulingError(f"No work order with id {work_order_id}")
        blocks = conn.execute(
            "SELECT b.id, r.name AS resource, b.start, b.end FROM schedule_blocks b "
            "JOIN resources r ON r.id = b.resource_id WHERE b.work_order_id = ? ORDER BY b.start",
            (work_order_id,),
        ).fetchall()
        return {**dict(wo), "blocks": [dict(b) for b in blocks]}

    @mcp.tool(annotations=READ)
    @_clear_errors
    def list_resources() -> list[dict]:
        """List machines and crews that can be scheduled."""
        return [dict(r) for r in conn.execute("SELECT * FROM resources ORDER BY id")]

    @mcp.tool(annotations=READ)
    @_clear_errors
    def find_next_slot(
        resource_id: Annotated[int, Field(gt=0)],
        hours: Annotated[float, Field(gt=0, le=24)],
        not_before: Annotated[str, Field(description="ISO datetime to start searching from")],
    ) -> dict:
        """Find the earliest weekday slot where a resource is free for the given number of hours."""
        start = scheduling.next_free_slot(conn, resource_id, hours, scheduling.parse_dt(not_before))
        return {"resource_id": resource_id, "start": start.isoformat(timespec="minutes"), "hours": hours}

    @mcp.tool(annotations=WRITE)
    @_clear_errors
    def schedule_work_order(
        work_order_id: Annotated[int, Field(gt=0)],
        resource_id: Annotated[int, Field(gt=0)],
        start: Annotated[str, Field(description="ISO datetime, e.g. 2026-11-02T08:00")],
        hours: Annotated[float, Field(gt=0, le=24)],
    ) -> dict:
        """Book a resource for a work order. Fails (and changes nothing) if the slot overlaps an existing booking."""
        guard_write()
        b = scheduling.schedule(conn, work_order_id, resource_id, scheduling.parse_dt(start), hours)
        return {"scheduled": True, "work_order_id": b.work_order_id, "resource_id": b.resource_id,
                "start": b.start.isoformat(timespec="minutes"), "end": b.end.isoformat(timespec="minutes")}

    @mcp.tool(annotations=DELETE)
    @_clear_errors
    def cancel_block(block_id: Annotated[int, Field(gt=0)]) -> dict:
        """Remove one scheduled block. The work order goes back to 'open' if it has no blocks left."""
        guard_write()
        row = conn.execute("SELECT * FROM schedule_blocks WHERE id = ?", (block_id,)).fetchone()
        if row is None:
            raise SchedulingError(f"No schedule block with id {block_id}")
        conn.execute("DELETE FROM schedule_blocks WHERE id = ?", (block_id,))
        left = conn.execute("SELECT COUNT(*) FROM schedule_blocks WHERE work_order_id = ?", (row["work_order_id"],)).fetchone()[0]
        if not left:
            conn.execute("UPDATE work_orders SET status = 'open' WHERE id = ? AND status = 'scheduled'", (row["work_order_id"],))
        conn.commit()
        return {"cancelled": block_id, "work_order_id": row["work_order_id"]}

    @mcp.tool(annotations=READ)
    @_clear_errors
    def utilization_report(start_date: str, end_date: str) -> list[dict]:
        """Booked vs. available hours per resource between two ISO dates (weekdays only)."""
        s, e = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if e < s:
            raise SchedulingError("end_date must be on or after start_date")
        return scheduling.utilization(conn, s, e)

    @mcp.tool(annotations=READ)
    @_clear_errors
    def late_risk_report(today: Annotated[str | None, Field(description="ISO date; defaults to the server's date")] = None) -> list[dict]:
        """Flag work orders likely to miss their due date."""
        return scheduling.late_risks(conn, date.fromisoformat(today) if today else datetime.now().date())

    return mcp


def main() -> None:
    conn = db.connect(DB_PATH)
    db.seed(conn)
    build_server(conn).run("stdio")


if __name__ == "__main__":
    main()
