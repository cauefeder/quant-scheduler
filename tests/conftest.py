# tests/conftest.py
"""Shared pytest fixtures for monorepo-level tests."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

MONOREPO_ROOT = Path(__file__).resolve().parents[1]
if str(MONOREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(MONOREPO_ROOT))


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Disposable SQLite path for a single test."""
    return tmp_path / "signal_tracker_test.db"


@pytest.fixture
def fresh_tracker(tmp_db_path: Path) -> Iterator[object]:
    """Import signal_tracker pointed at a temp DB; yields the module."""
    import importlib
    import signal_tracker  # noqa: WPS433 — dynamic monorepo import

    importlib.reload(signal_tracker)
    signal_tracker.DB_PATH = tmp_db_path
    signal_tracker.init_db()
    yield signal_tracker
