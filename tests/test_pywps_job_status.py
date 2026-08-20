from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


SCRIPT = (
    Path(__file__).parents[1]
    / "roles"
    / "wps_tools"
    / "files"
    / "pywps-job-status.py"
)
SPEC = importlib.util.spec_from_file_location("pywps_job_status", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Status:
    ACCEPTED = 0
    STARTED = 1
    PAUSED = 2
    SUCCEEDED = 3
    FAILED = 4
    DISMISSED = 5


def record(job_id, process, status, started, ended=None):
    return SimpleNamespace(
        uuid=job_id,
        identifier=process,
        status=status,
        time_start=started,
        time_end=ended,
    )


class PywpsJobStatusTests(unittest.TestCase):
    def test_parse_window_supports_minutes_hours_and_days(self):
        self.assertEqual(MODULE.parse_window("30m"), timedelta(minutes=30))
        self.assertEqual(MODULE.parse_window("24h"), timedelta(hours=24))
        self.assertEqual(MODULE.parse_window("7d"), timedelta(days=7))
        with self.assertRaises(ValueError):
            MODULE.parse_window("0h")

    def test_summary_keeps_old_active_jobs_out_of_window_totals(self):
        now = datetime(2026, 8, 20, 20, tzinfo=timezone.utc)
        records = [
            record(
                "recent-ok",
                "subset",
                Status.SUCCEEDED,
                now - timedelta(minutes=10),
                now - timedelta(minutes=9),
            ),
            record(
                "recent-fail",
                "orchestrate",
                Status.FAILED,
                now - timedelta(minutes=20),
                now - timedelta(minutes=18),
            ),
            record(
                "recent-run",
                "subset",
                Status.STARTED,
                now - timedelta(minutes=30),
            ),
            record(
                "old-run",
                "orchestrate",
                Status.STARTED,
                now - timedelta(hours=3),
            ),
            record(
                "old-ok",
                "subset",
                Status.SUCCEEDED,
                now - timedelta(hours=3),
                now - timedelta(hours=2),
            ),
        ]

        report = MODULE.summarize(
            records,
            Status,
            now=now,
            window=timedelta(hours=1),
        )

        self.assertEqual(report["requests"], 3)
        self.assertEqual(report["successful"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["active_in_window"], 1)
        self.assertEqual(len(report["active_jobs"]), 2)
        orchestrate = next(
            item for item in report["processes"] if item["identifier"] == "orchestrate"
        )
        self.assertEqual(orchestrate["requests"], 1)
        self.assertEqual(orchestrate["active"], 1)

    def test_text_output_is_compact_and_labels_active_scope(self):
        now = datetime(2026, 8, 20, 20, tzinfo=timezone.utc)
        report = MODULE.summarize(
            [
                record(
                    "12345678-rest",
                    "subset",
                    Status.STARTED,
                    now - timedelta(minutes=5),
                )
            ],
            Status,
            now=now,
            window=timedelta(hours=1),
        )
        output = io.StringIO()

        with redirect_stdout(output):
            MODULE.print_report(report, service="rook", window_label="1h", top=10)

        text = output.getvalue()
        self.assertIn("PyWPS database — rook", text)
        self.assertIn("Window: last 1h", text)
        self.assertIn("Processes — window requests; active includes all ages", text)
        self.assertIn("Active jobs — all ages (1)", text)
        self.assertIn("12345678", text)


if __name__ == "__main__":
    unittest.main()
