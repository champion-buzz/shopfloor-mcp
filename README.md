# shopfloor-mcp

[![tests](https://github.com/champion-buzz/shopfloor-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/champion-buzz/shopfloor-mcp/actions/workflows/tests.yml)

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants such as Claude and Cursor **read and schedule manufacturing work orders**, without double-booking a machine or quietly changing data.

I built a React/FastAPI scheduling platform during my internship at Micro-ant. This project asks the next question: *what does it take to let an AI agent work with a production schedule safely?*

```
You:     Which orders are at risk this week, and can you fit the gearbox housing on a mill on Monday?
Claude:  → late_risk_report → list_resources → find_next_slot(resource 1, 6h) → schedule_work_order(...)
         "Order #3 (Mesa Drones, due Nov 3) had nothing booked. I scheduled it on CNC Mill 1, Nov 2 08:00–14:00."
```

## Tools

| Tool | What it does | Safety hint |
|---|---|---|
| `list_work_orders` | Filter by status and due date, most urgent first | read-only |
| `get_work_order` | One order plus its scheduled blocks | read-only |
| `list_resources` | Machines and crews | read-only |
| `find_next_slot` | Earliest free weekday slot for N hours on a resource | read-only |
| `schedule_work_order` | Book a resource; **rejects overlaps** instead of double-booking | write |
| `cancel_block` | Remove a booking; the order reverts to `open` if nothing is left | destructive |
| `utilization_report` | Booked vs. available hours per resource | read-only |
| `late_risk_report` | Orders likely to miss their due date, and why | read-only |

## Guardrails for agents

- **Typed, validated inputs.** Pydantic constraints (for example `0 < hours ≤ 24`, positive IDs, ISO datetimes) are published in each tool's JSON schema, so bad calls are rejected before any code runs.
- **No silent conflicts.** Scheduling checks the resource's existing bookings and fails with the clashing block, so the agent can explain the problem or pick another slot.
- **Errors the model can act on.** Expected failures come back as tool errors with a plain reason (`Resource 4 is already booked: …`), not a stack trace.
- **Tool annotations.** Each tool declares `read_only_hint` and `destructive_hint`, so clients can ask the user before a destructive call.
- **Read-only mode.** Set `SHOPFLOOR_READ_ONLY=1` to expose the data while blocking every write.

## Run it

```bash
git clone https://github.com/champion-buzz/shopfloor-mcp && cd shopfloor-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # 13 tests
shopfloor-mcp        # starts the server on stdio (creates shopfloor.db with demo data)
```

### Use it from Claude Desktop

Add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "shopfloor": {
      "command": "/absolute/path/to/shopfloor-mcp/.venv/bin/shopfloor-mcp",
      "env": { "SHOPFLOOR_DB": "/absolute/path/to/shopfloor.db" }
    }
  }
}
```

Restart Claude and ask: *"Show me open work orders due before Nov 6 and schedule the most urgent one on the first free mill."*

You can also inspect it with the MCP Inspector: `npx @modelcontextprotocol/inspector shopfloor-mcp`

## Design

```
src/shopfloor_mcp/
├── db.py          SQLite schema and demo seed data
├── scheduling.py  pure scheduling logic: overlaps, next free slot, utilization, late risk
└── server.py      MCP layer: tool definitions, validation, annotations, read-only guard
tests/
├── test_scheduling.py  logic tests
└── test_server.py      tests through the MCP tool interface
```

The scheduling logic doesn't depend on MCP, so it's tested on its own and could sit behind a REST API (for example FastAPI) unchanged.

## Ideas for next steps

- Split jobs longer than one workday across several days automatically
- Streamable HTTP transport with auth, for a shared deployment
- An eval set of natural-language requests to measure how well an LLM picks tools

## License

MIT
