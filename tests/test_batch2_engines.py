"""Unit tests for Batch 2 sysadmin engines:
- service_crash_correlation.py
- log_saturation.py
- wct_engine.py
- minidump_parser.py
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from modules.crash_dumps.minidump_parser import parse_minidump_modules
from modules.diagnose.log_saturation import inspect_channel_retention
from modules.diagnose.service_crash_correlation import (
    ServiceCrashEvent,
    correlate_with_app_errors,
    get_recent_service_crashes,
)
from modules.process.wct_engine import inspect_thread_wait_chain


class TestBatch2Engines(unittest.TestCase):
    """Test Batch 2 diagnostics and crash correlation engines."""

    @patch("subprocess.run")
    def test_service_crash_correlation(self, mock_subproc):
        xml_system = """
        <Events xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
            <Event>
                <System><EventID>7031</EventID><TimeCreated SystemTime="2026-11-20T12:00:00Z"/></System>
                <EventData>
                    <Data>SearchIndexer</Data>
                    <Data>Restart service</Data>
                    <Data>1</Data>
                </EventData>
            </Event>
        </Events>
        """
        mock_subproc.return_value = MagicMock(returncode=0, stdout=xml_system)
        crashes = get_recent_service_crashes(max_events=1)
        self.assertEqual(len(crashes), 1)
        self.assertEqual(crashes[0].service_name, "SearchIndexer")

    @patch("modules.diagnose.log_saturation.get_channel_config")
    @patch("modules.diagnose.log_saturation.get_oldest_event_timestamp")
    def test_log_saturation_sentinel(self, mock_oldest, mock_cfg):
        mock_cfg.return_value = {"enabled": "true", "maxsize": "20971520"}
        from datetime import datetime, timedelta, timezone
        # 1 day ago -> saturated
        mock_oldest.return_value = datetime.now(timezone.utc) - timedelta(days=1.2)

        ret = inspect_channel_retention("System")
        self.assertTrue(ret.is_saturated)
        self.assertIn("Fast Wrap", ret.status_label)

    def test_wct_engine_current_thread(self):
        import threading
        tid = threading.get_native_id()
        report = inspect_thread_wait_chain(tid)
        self.assertGreaterEqual(report.chain_length, 1)
        self.assertFalse(report.is_deadlocked)

    def test_minidump_parser_nonexistent(self):
        res = parse_minidump_modules(r"C:\fake\dump.dmp")
        self.assertFalse(res.is_valid)
        self.assertEqual(res.verdict, "File not found")


if __name__ == "__main__":
    unittest.main()
