"""Tests for the scheduler's enabled-flag behavior.

Other scheduler concerns (subprocess running, Telegram forwarding,
catch-up loop) require network or process-level mocking and are not
exercised here. The enabled-flag is the one piece small enough to TDD
in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import scheduler


def test_run_project_skips_when_enabled_false(caplog: pytest.LogCaptureFixture) -> None:
    """`enabled: False` short-circuits the runner and returns True (not a failure)."""
    project = {
        "name": "ZZZ Test Paused",
        "cwd": Path("D:/nonexistent-but-shouldnt-matter"),
        "cmd": ["echo", "should-never-run"],
        "enabled": False,
        "disabled_reason": "test only",
    }
    with patch("scheduler.subprocess.run") as run_mock:
        result = scheduler.run_project(project)
    assert result is True
    run_mock.assert_not_called()


def test_run_project_runs_when_enabled_true_or_unset(tmp_path: Path) -> None:
    """Default (no `enabled` key) → project runs. We patch subprocess.run so we
    don't actually shell out."""
    project = {
        "name": "ZZZ Test Active",
        "cwd": tmp_path,           # exists
        "cmd": ["echo", "hi"],
        # no enabled key → defaults True
    }
    with patch("scheduler.subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = ""
        run_mock.return_value.stderr = ""
        result = scheduler.run_project(project)
    assert result is True
    run_mock.assert_called_once()


def test_paused_projects_in_PROJECTS_have_disabled_reason() -> None:
    """Any project marked enabled=False must carry a disabled_reason for
    operator transparency — prevents silent disables sneaking in.
    """
    for project in scheduler.PROJECTS:
        if project.get("enabled") is False:
            assert project.get("disabled_reason"), (
                f"{project['name']} marked disabled but no disabled_reason given"
            )


def test_e2_losing_systems_are_paused() -> None:
    """Regression: Poly Kelly Scraper + Poly2 Kelly Bot must remain disabled.
    They were paused after the E2 P&L report flagged them as value-destructive.
    Flipping them back on requires intentional code change + this test update.
    """
    expected_paused = {"Poly Kelly Scraper", "Poly2 Kelly Bot"}
    actually_paused = {
        p["name"] for p in scheduler.PROJECTS if p.get("enabled") is False
    }
    assert expected_paused.issubset(actually_paused), (
        f"Re-enabled losing systems without justification: "
        f"{expected_paused - actually_paused}"
    )
