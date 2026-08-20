from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).parents[1]
    / "roles"
    / "wps_tools"
    / "files"
    / "pywps-db-report.py"
)
SPEC = importlib.util.spec_from_file_location("pywps_db_report", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
UTC = timezone.utc


class DatabaseReportTests(unittest.TestCase):
    def setUp(self):
        self.statuses = argparse.Namespace(
            ACCEPTED=0,
            STARTED=1,
            PAUSED=2,
            SUCCEEDED=4,
            FAILED=5,
        )
        self.period = MODULE.TimeRange(
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 8, 6, 23, 59, 59, 999999, tzinfo=UTC),
            "2026-08-01/2026-08-06",
        )

    def record(self, **overrides):
        values = dict(
            uuid="123e4567-e89b-42d3-a456-426614174000",
            operation="execute",
            identifier="orchestrate",
            time_start=datetime(2026, 8, 2, 10, tzinfo=UTC),
            time_end=datetime(2026, 8, 2, 10, 1, tzinfo=UTC),
            status=4,
            message="done",
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_date_range_includes_entire_end_date(self):
        period = MODULE.parse_time_range("2026-08-01/2026-08-06")
        self.assertEqual(period.start.astimezone().date().isoformat(), "2026-08-01")
        self.assertEqual(period.end.astimezone().date().isoformat(), "2026-08-06")
        self.assertEqual(period.end.microsecond, 999999)

    def test_single_year_month_and_date_select_complete_period(self):
        year = MODULE.parse_time_range("2026")
        self.assertEqual(year.start.astimezone().date().isoformat(), "2026-01-01")
        self.assertEqual(year.end.astimezone().date().isoformat(), "2026-12-31")

        month = MODULE.parse_time_range("2024-02")
        self.assertEqual(month.start.astimezone().date().isoformat(), "2024-02-01")
        self.assertEqual(month.end.astimezone().date().isoformat(), "2024-02-29")

        day = MODULE.parse_time_range("2026-08-12")
        self.assertEqual(day.start.astimezone().date().isoformat(), "2026-08-12")
        self.assertEqual(day.end.astimezone().date().isoformat(), "2026-08-12")

    def test_range_boundaries_accept_year_month_and_open_ends(self):
        period = MODULE.parse_time_range("2025/2026-08")
        self.assertEqual(period.start.astimezone().date().isoformat(), "2025-01-01")
        self.assertEqual(period.end.astimezone().date().isoformat(), "2026-08-31")

        from_month = MODULE.parse_time_range("2026-08/")
        self.assertEqual(
            from_month.start.astimezone().date().isoformat(), "2026-08-01"
        )
        self.assertIsNone(from_month.end)

        through_month = MODULE.parse_time_range("/2026-08")
        self.assertIsNone(through_month.start)
        self.assertEqual(
            through_month.end.astimezone().date().isoformat(), "2026-08-31"
        )

        all_time = MODULE.parse_time_range("/")
        self.assertIsNone(all_time.start)
        self.assertIsNone(all_time.end)

    def test_rejects_reversed_and_malformed_ranges(self):
        with self.assertRaisesRegex(ValueError, "single range value"):
            MODULE.parse_time_range("2026-08-01T12:00:00")
        with self.assertRaisesRegex(ValueError, "at most one '/'"):
            MODULE.parse_time_range("2026/2027/2028")
        with self.assertRaisesRegex(ValueError, "must not be after"):
            MODULE.parse_time_range("2026-08-07/2026-08-06")

    def test_from_and_to_options_are_independently_optional(self):
        with mock.patch.object(MODULE.Path, "is_file", return_value=True):
            from_args = MODULE.parse_args(
                ["--config", "/etc/pywps/rook.cfg", "--from", "2026-08"]
            )
            to_args = MODULE.parse_args(
                ["--config", "/etc/pywps/rook.cfg", "--to", "2026"]
            )
            all_args = MODULE.parse_args(["--config", "/etc/pywps/rook.cfg"])
        self.assertEqual(
            from_args.period.start.astimezone().date().isoformat(), "2026-08-01"
        )
        self.assertIsNone(from_args.period.end)
        self.assertIsNone(to_args.period.start)
        self.assertEqual(
            to_args.period.end.astimezone().date().isoformat(), "2026-12-31"
        )
        self.assertIsNone(all_args.period.start)
        self.assertIsNone(all_args.period.end)

    def test_range_position_cannot_be_combined_with_from_or_to(self):
        with mock.patch.object(MODULE.Path, "is_file", return_value=True):
            with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                MODULE.parse_args(
                    [
                        "--config",
                        "/etc/pywps/rook.cfg",
                        "2026",
                        "--from",
                        "2025",
                    ]
                )

    def test_aggregates_statuses_processes_durations_and_errors(self):
        records = [
            self.record(),
            self.record(
                uuid="223e4567-e89b-42d3-a456-426614174000",
                time_start=datetime(2026, 8, 3, 10, tzinfo=UTC),
                time_end=datetime(2026, 8, 3, 10, 2, tzinfo=UTC),
                status=5,
                message="Dataset unavailable",
            ),
            self.record(
                uuid="323e4567-e89b-42d3-a456-426614174000",
                identifier="subset",
                time_start=datetime(2026, 8, 4, 10, tzinfo=UTC),
                time_end=datetime(2026, 8, 4, 10, 3, tzinfo=UTC),
                status=5,
                message="Dataset unavailable",
            ),
            self.record(
                uuid="423e4567-e89b-42d3-a456-426614174000",
                time_start=datetime(2026, 8, 5, 10, tzinfo=UTC),
                time_end=None,
                status=1,
                message="running",
            ),
            self.record(time_start=datetime(2026, 7, 31, tzinfo=UTC)),
        ]

        report = MODULE.summarize(records, self.statuses, self.period)

        self.assertEqual(report["requests"]["total"], 4)
        self.assertEqual(report["requests"]["successful"], 1)
        self.assertEqual(report["requests"]["failed"], 2)
        self.assertEqual(report["requests"]["running"], 1)
        self.assertEqual(report["requests"]["success_rate_percent"], 33.33)
        self.assertEqual(report["duration_seconds"]["total"], 360)
        self.assertEqual(report["successful_duration_seconds"]["count"], 1)
        self.assertEqual(
            report["successful_duration_seconds"]["from_1_to_under_10_minutes"],
            1,
        )
        self.assertEqual(report["successful_duration_seconds"]["maximum"], 60)
        self.assertEqual([item["identifier"] for item in report["processes"]], [
            "orchestrate",
            "subset",
        ])
        self.assertEqual(report["errors"][0]["count"], 2)
        self.assertEqual(report["errors"][0]["message"], "Dataset unavailable")

    def test_error_message_json_encoding_keeps_report_one_line_per_error(self):
        report = MODULE.summarize(
            [self.record(status=5, message="first line\nsecond line")],
            self.statuses,
            self.period,
        )
        with mock.patch("sys.stdout") as stdout:
            MODULE.print_report(report, failures=True)
        output = "".join(call.args[0] + "\n" for call in stdout.write.call_args_list)
        self.assertIn('"first line\\nsecond line"', output)

    def test_text_report_limits_long_error_messages(self):
        message = "x" * 400
        self.assertEqual(len(MODULE.display_error_message(message)), 300)
        self.assertTrue(MODULE.display_error_message(message).endswith(" [..]"))

    def test_text_report_handles_an_empty_range(self):
        report = MODULE.summarize([], self.statuses, self.period)
        with mock.patch("sys.stdout") as stdout:
            MODULE.print_report(report)
        output = "".join(call.args[0] + "\n" for call in stdout.write.call_args_list)
        self.assertIn("Total", output)
        self.assertIn("Success rate: n/a", output)
        self.assertNotIn("Failure details", output)

        with mock.patch("sys.stdout") as stdout:
            MODULE.print_report(report, failures=True)
        output = "".join(call.args[0] + "\n" for call in stdout.write.call_args_list)
        self.assertIn("Failure details (0 failures, 0 unique messages)", output)

    def test_failure_details_are_optional_and_limited_by_top(self):
        report = MODULE.summarize(
            [
                self.record(status=5, message="common"),
                self.record(status=5, message="common"),
                self.record(status=5, message="rare"),
            ],
            self.statuses,
            self.period,
        )
        with mock.patch("sys.stdout") as stdout:
            MODULE.print_report(report, failures=True, top=1)
        output = "".join(call.args[0] + "\n" for call in stdout.write.call_args_list)
        self.assertIn("showing 1, increase --top to see more", output)
        self.assertIn('2x  "common"', output)
        self.assertNotIn('1x  "rare"', output)

    def test_successful_duration_distribution_uses_distinct_ranges(self):
        distribution = MODULE.duration_distribution(
            [30, 60, 599.9, 600, 1799.9, 1800, 7200]
        )
        self.assertEqual(
            distribution,
            {
                "count": 7,
                "under_1_minute": 1,
                "from_1_to_under_10_minutes": 2,
                "from_10_to_under_30_minutes": 2,
                "30_minutes_or_more": 2,
                "maximum": 7200,
            },
        )

    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX timezone control")
    def test_naive_uuid1_time_uses_writer_offset(self):
        previous_timezone = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            record = self.record(
                uuid="843b6f0e-94f4-11f1-aca4-fa163e934c9b",
                time_start=datetime(2026, 8, 10, 21, 48, 53, 662211),
            )
            self.assertEqual(
                MODULE.database_timestamp(record, record.time_start),
                datetime(2026, 8, 10, 19, 48, 53, 662211, tzinfo=UTC),
            )
        finally:
            if previous_timezone is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous_timezone
            time.tzset()

    def test_json_default_serializes_datetimes(self):
        value = json.dumps({"at": self.period.start}, default=MODULE.json_default)
        self.assertEqual(value, '{"at": "2026-08-01T00:00:00+00:00"}')


if __name__ == "__main__":
    unittest.main()
