import asyncio

from mcp.server.mcpserver.exceptions import ToolError

from shopfloor_mcp.server import build_server


def call(server, name, **args):
    """Return (is_error, text) the way an MCP client would see the result."""
    try:
        result = asyncio.run(server.call_tool(name, args))
    except ToolError as exc:
        return True, str(exc)
    return False, "".join(getattr(c, "text", "") for c in result.content)


def test_tools_are_listed_with_safety_annotations(conn):
    tools = {t.name: t for t in asyncio.run(build_server(conn).list_tools())}
    assert {"list_work_orders", "schedule_work_order", "cancel_block", "late_risk_report"} <= tools.keys()
    assert tools["list_work_orders"].annotations.read_only_hint is True
    assert tools["cancel_block"].annotations.destructive_hint is True


def test_schedule_and_read_back(conn):
    s = build_server(conn)
    err, _ = call(s, "schedule_work_order", work_order_id=1, resource_id=1, start="2026-11-02T08:00", hours=4)
    assert not err
    err, text = call(s, "get_work_order", work_order_id=1)
    assert not err and "CNC Mill 1" in text and '"scheduled"' in text


def test_conflict_returns_tool_error(conn):
    s = build_server(conn)
    call(s, "schedule_work_order", work_order_id=1, resource_id=1, start="2026-11-02T08:00", hours=4)
    err, text = call(s, "schedule_work_order", work_order_id=2, resource_id=1, start="2026-11-02T09:00", hours=1)
    assert err and "already booked" in text


def test_invalid_input_is_rejected(conn):
    err, _ = call(build_server(conn), "schedule_work_order", work_order_id=1, resource_id=1, start="2026-11-02T08:00", hours=-2)
    assert err


def test_read_only_mode_blocks_writes(conn):
    s = build_server(conn, read_only=True)
    err, text = call(s, "schedule_work_order", work_order_id=1, resource_id=1, start="2026-11-02T08:00", hours=4)
    assert err and "read-only" in text
    assert conn.execute("SELECT COUNT(*) FROM schedule_blocks").fetchone()[0] == 0
    err, _ = call(s, "list_work_orders")
    assert not err
