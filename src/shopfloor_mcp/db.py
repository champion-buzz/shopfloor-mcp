"""SQLite storage for work orders, resources and scheduled blocks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,                -- machine | crew
    hours_per_day REAL NOT NULL DEFAULT 8
);
CREATE TABLE IF NOT EXISTS work_orders (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    customer    TEXT NOT NULL,
    due_date    TEXT NOT NULL,                -- ISO date
    est_hours   REAL NOT NULL CHECK (est_hours > 0),
    priority    INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),  -- 1 = highest
    status      TEXT NOT NULL DEFAULT 'open'  -- open | scheduled | done
);
CREATE TABLE IF NOT EXISTS schedule_blocks (
    id            INTEGER PRIMARY KEY,
    work_order_id INTEGER NOT NULL REFERENCES work_orders(id),
    resource_id   INTEGER NOT NULL REFERENCES resources(id),
    start         TEXT NOT NULL,              -- ISO datetime
    end           TEXT NOT NULL               -- ISO datetime
);
"""

SEED_RESOURCES = [
    ("CNC Mill 1", "machine", 8),
    ("CNC Mill 2", "machine", 8),
    ("Laser Cutter", "machine", 10),
    ("Assembly Crew A", "crew", 8),
]

SEED_WORK_ORDERS = [
    ("Bracket batch (500 pcs)", "Acme Robotics", "2026-11-04", 12, 1),
    ("Steel enclosure panels", "Desert Solar", "2026-11-06", 9, 2),
    ("Prototype gearbox housing", "Mesa Drones", "2026-11-03", 6, 1),
    ("Mounting plates (200 pcs)", "Acme Robotics", "2026-11-12", 7, 3),
    ("Control cabinet assembly", "Valley Water", "2026-11-09", 16, 2),
    ("Spare shaft couplings", "Mesa Drones", "2026-11-20", 4, 4),
]


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def seed(conn: sqlite3.Connection) -> None:
    """Insert demo data if the database is empty."""
    if conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]:
        return
    conn.executemany("INSERT INTO resources (name, kind, hours_per_day) VALUES (?, ?, ?)", SEED_RESOURCES)
    conn.executemany(
        "INSERT INTO work_orders (title, customer, due_date, est_hours, priority) VALUES (?, ?, ?, ?, ?)",
        SEED_WORK_ORDERS,
    )
    conn.commit()
