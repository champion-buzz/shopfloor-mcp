from datetime import date, datetime

import pytest

from shopfloor_mcp import scheduling
from shopfloor_mcp.scheduling import SchedulingError

MON = datetime(2026, 11, 2, 8, 0)


def test_schedule_marks_order_scheduled(conn):
    scheduling.schedule(conn, 1, 1, MON, 4)
    assert conn.execute("SELECT status FROM work_orders WHERE id = 1").fetchone()[0] == "scheduled"


def test_overlap_is_rejected_and_nothing_written(conn):
    scheduling.schedule(conn, 1, 1, MON, 4)
    with pytest.raises(SchedulingError, match="already booked"):
        scheduling.schedule(conn, 2, 1, datetime(2026, 11, 2, 10, 0), 2)
    assert conn.execute("SELECT COUNT(*) FROM schedule_blocks").fetchone()[0] == 1


def test_back_to_back_blocks_do_not_conflict(conn):
    scheduling.schedule(conn, 1, 1, MON, 4)
    scheduling.schedule(conn, 2, 1, datetime(2026, 11, 2, 12, 0), 4)


def test_unknown_ids_are_rejected(conn):
    with pytest.raises(SchedulingError, match="No work order"):
        scheduling.schedule(conn, 999, 1, MON, 1)
    with pytest.raises(SchedulingError, match="No resource"):
        scheduling.schedule(conn, 1, 999, MON, 1)


def test_next_free_slot_skips_bookings_and_weekends(conn):
    scheduling.schedule(conn, 1, 1, MON, 8)  # Monday fully booked on mill 1
    assert scheduling.next_free_slot(conn, 1, 4, MON) == datetime(2026, 11, 3, 8, 0)
    friday_late = datetime(2026, 11, 6, 15, 0)
    assert scheduling.next_free_slot(conn, 1, 4, friday_late) == datetime(2026, 11, 9, 8, 0)


def test_job_longer_than_workday_is_rejected(conn):
    with pytest.raises(SchedulingError, match="split the job"):
        scheduling.next_free_slot(conn, 1, 9, MON)


def test_utilization(conn):
    scheduling.schedule(conn, 1, 1, MON, 8)
    report = {r["resource"]: r for r in scheduling.utilization(conn, date(2026, 11, 2), date(2026, 11, 6))}
    assert report["CNC Mill 1"]["booked_hours"] == 8
    assert report["CNC Mill 1"]["available_hours"] == 40
    assert report["CNC Mill 1"]["utilization_pct"] == 20.0


def test_late_risks(conn):
    # Order 3 is due 2026-11-03 and has nothing booked.
    risks = scheduling.late_risks(conn, date(2026, 11, 1))
    assert 3 in {r["work_order_id"] for r in risks}
    # Booking order 4 to end after its due date is flagged too.
    scheduling.schedule(conn, 4, 2, datetime(2026, 11, 13, 8, 0), 7)
    assert any(r["work_order_id"] == 4 and "after due date" in r["reason"]
               for r in scheduling.late_risks(conn, date(2026, 11, 1)))
