import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shop.harness import Harness  # noqa: E402


@pytest.fixture
def h(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return Harness(db_path=":memory:", admin_id=999)
