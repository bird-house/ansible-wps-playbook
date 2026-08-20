from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


FILES = Path(__file__).parents[1] / "roles" / "wps_tools" / "files"
sys.path.insert(0, str(FILES))
SCRIPT = FILES / "wps-event-statistics.py"
SPEC = importlib.util.spec_from_file_location("wps_event_statistics", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def request(job: str, outcome: str, message: str = "") -> dict[str, object]:
    failures = [{"message": message}] if message else []
    return {
        "schema_version": 1,
        "record_type": "request",
        "service": "rook",
        "job_id": job,
        "finished_at": "2026-08-19T12:00:00+00:00",
        "outcome": outcome,
        "duration_seconds": 60,
        "failures": failures,
    }


def operation(event: str, job: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "operation",
        "service": "rook",
        "job_id": job,
        "recorded_at": "2026-08-19T12:05:00+00:00",
        "event": event,
        "level": "warning",
        "message": event,
    }


class EventStatisticsTests(unittest.TestCase):
    def test_builds_daily_request_and_operation_statistics(self):
        records = [
            request("one", "successful"),
            request("two", "failed", "Detected an oom-kill event"),
            request("three", "failed", "Cancelled due to time limit"),
            operation("job-recovered", "two"),
            operation("job-long-running", "four"),
            operation("job-long-running", "four"),
        ]
        row = MODULE.daily_rows(records, "rook")["2026-08-19"]
        self.assertEqual(row["requests"], "3")
        self.assertEqual(row["successful"], "1")
        self.assertEqual(row["failed"], "2")
        self.assertEqual(row["memory_failures"], "1")
        self.assertEqual(row["timeout_failures"], "1")
        self.assertEqual(row["recovered_jobs"], "1")
        self.assertEqual(row["long_running_jobs"], "1")

    def test_update_preserves_older_aggregate_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            event_log = root / "rook-events.jsonl"
            csv_path = root / "rook-daily.csv"
            MODULE.write_csv(
                csv_path,
                {
                    "2025-01-01": {
                        field: "2025-01-01" if field == "date" else "rook" if field == "service" else "0"
                        for field in MODULE.FIELDS
                    }
                },
                keep_days=0,
            )
            event_log.write_text(json.dumps(request("one", "successful")) + "\n")
            rows, errors = MODULE.update_rows(csv_path, [event_log], "rook", 0)
        self.assertEqual(errors, [])
        self.assertIn("2025-01-01", rows)
        self.assertEqual(rows["2026-08-19"]["requests"], "1")

    def test_summary_is_suitable_for_json_and_human_reports(self):
        rows = list(MODULE.daily_rows([request("one", "successful")], "rook").values())
        report = MODULE.summary(rows)
        self.assertEqual(report["requests"], 1)
        self.assertEqual(report["success_rate"], 100.0)
        json.dumps(report)


if __name__ == "__main__":
    unittest.main()
