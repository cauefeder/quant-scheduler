import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location("scheduler_under_test", Path(__file__).resolve().parents[1] / "scheduler.py")
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)


class ReliabilityTests(unittest.TestCase):
    def test_claim_persists_per_slot_and_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "claims.sqlite3"
            now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
            self.assertTrue(scheduler.claim_slot((12, 0), now, db))
            self.assertFalse(scheduler.claim_slot((12, 0), now, db))
            self.assertTrue(scheduler.claim_slot((22, 0), now, db))
            self.assertTrue(scheduler.claim_slot((12, 0), now.replace(day=19), db))

    def test_delivery_failure_means_project_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = dict(name="test", cwd=tmp, cmd=["unused"], capture_to_telegram=True)
            with patch.object(scheduler.subprocess, "run", return_value=Mock(returncode=0, stdout="report")), patch.object(scheduler, "_tg_send", return_value=False):
                self.assertFalse(scheduler.run_project(project))

    def test_telegram_api_rejection(self):
        response = Mock(status=200)
        response.read.return_value = b'{"ok":false}'
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch.object(scheduler.urllib.request, "urlopen", return_value=context):
            self.assertFalse(scheduler._tg_send("test", "fake-token", "fake-chat"))

    def test_dry_run_does_not_send_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = dict(name="test", cwd=tmp, cmd=["unused"], capture_to_telegram=True)
            with patch.object(scheduler, "PROJECTS", [project]), patch.object(scheduler, "_tg_send") as send, patch.object(scheduler.subprocess, "run") as run:
                self.assertTrue(scheduler.run_all(dry_run=True))
                send.assert_not_called()
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
