import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shopfloor_mcp import db  # noqa: E402


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.seed(c)
    yield c
    c.close()
