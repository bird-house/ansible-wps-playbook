from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "slurm-job-monitor.py"
SPEC = importlib.util.spec_from_file_location("slurm_job_monitor", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SlurmJobMonitorTests(unittest.TestCase):
    def test_parse_elapsed_supports_slurm_duration_formats(self):
        self.assertEqual(MODULE.parse_elapsed("05:30"), 330)
        self.assertEqual(MODULE.parse_elapsed("02:05:30"), 7530)
        self.assertEqual(MODULE.parse_elapsed("2-02:05:30"), 180330)

    def test_list_jobs_scopes_squeue_to_user_and_active_states(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="101|RUNNING|01:30:00\n102|PENDING|0:00\n"
            )
        )

        jobs = MODULE.list_jobs("wps", runner)

        self.assertEqual(
            jobs,
            [
                MODULE.SlurmJob("101", "RUNNING", 5400),
                MODULE.SlurmJob("102", "PENDING", None),
            ],
        )
        command = runner.call_args.args[0]
        self.assertEqual(command[0], "squeue")
        self.assertEqual(command[command.index("--user") + 1], "wps")
        self.assertEqual(
            command[command.index("--states") + 1],
            "PENDING,RUNNING",
        )

    def test_inspection_counts_queue_and_warns_for_long_running_jobs(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                stdout=(
                    "100|RUNNING|03:59:59\n"
                    "101|RUNNING|04:00:00\n"
                    "102|PENDING|0:00\n"
                ),
            )
        )
        logger = mock.Mock()

        summary = MODULE.inspect_jobs("wps", 14400, 20, logger, runner)

        self.assertEqual(
            (summary.running, summary.pending, summary.total, summary.long_running),
            (2, 1, 3, 1),
        )
        logger.warning.assert_called_once_with(
            "job=%s state=RUNNING finding=long-running elapsed_seconds=%d "
            "warning_seconds=%g",
            "101",
            14400,
            14400,
        )

    def test_list_capacity_uses_sinfo_node_and_partition_fields(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="localhost|up|idle|none\n"
            )
        )

        capacity = MODULE.list_capacity(runner)

        self.assertEqual(
            capacity,
            [MODULE.SlurmCapacity("localhost", "up", "idle", "none")],
        )
        self.assertEqual(
            runner.call_args.args[0],
            [
                "sinfo",
                "--noheader",
                "--Node",
                "--format",
                "%N|%a|%T|%E",
            ],
        )

    def test_capacity_accepts_working_states_and_flags_bad_states(self):
        healthy = [
            MODULE.SlurmCapacity("node1", "up", state, "none")
            for state in ("idle", "allocated", "allocated+", "mixed", "completing")
        ]
        self.assertEqual(MODULE.capacity_issues(healthy), [])

        unhealthy = [
            MODULE.SlurmCapacity("node1", "down", "idle", "maintenance"),
            MODULE.SlurmCapacity("node2", "up", "down*", "not responding"),
            MODULE.SlurmCapacity("node3", "up", "drained", "operator request"),
            MODULE.SlurmCapacity("node4", "up", "allocated*+", "not responding"),
        ]
        issues = MODULE.capacity_issues(unhealthy)
        self.assertEqual(len(issues), 4)
        self.assertIn("partition_state=down", issues[0])
        self.assertIn("node_state=down*", issues[1])
        self.assertIn("node_state=drained", issues[2])
        self.assertIn("node_state=allocated*+", issues[3])

    def test_missing_capacity_is_a_red_alert_condition(self):
        self.assertEqual(
            MODULE.capacity_issues([]),
            ["reason=no-capacity-records"],
        )

    def test_pending_queue_critical_uses_configured_threshold(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="100|PENDING|0:00\n101|PENDING|0:00\n"
            )
        )
        logger = mock.Mock()

        summary = MODULE.inspect_jobs("wps", 14400, 2, logger, runner)

        self.assertEqual((summary.running, summary.pending), (0, 2))
        logger.critical.assert_called_once_with(
            "finding=pending-queue-full pending=%d critical_threshold=%d",
            2,
            2,
        )

    def test_monitor_never_invokes_scancel(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="100|RUNNING|07:00:00\n"
            )
        )

        MODULE.inspect_jobs("wps", 14400, 20, mock.Mock(), runner)

        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.args[0][0], "squeue")

    def test_logging_keeps_normal_summary_out_of_cron_output(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            MODULE.logging, "basicConfig"
        ) as basic_config:
            MODULE.configure_logging(Path(directory) / "slurm.log")

        handlers = basic_config.call_args.kwargs["handlers"]
        self.assertEqual(handlers[0].level, MODULE.logging.CRITICAL)
        self.assertEqual(handlers[1].level, MODULE.logging.INFO)
        handlers[1].close()

    def test_red_alert_is_atomic_readable_json_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            alert = Path(directory) / "run" / "slurm-red-alert.json"

            MODULE.write_alert(
                alert,
                "pending-queue-full",
                pending=20,
                threshold=20,
            )

            payload = json.loads(alert.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "red")
            self.assertEqual(payload["reason"], "pending-queue-full")
            self.assertEqual(payload["pending"], 20)
            self.assertEqual(payload["threshold"], 20)
            self.assertIn("checked_at", payload)
            self.assertEqual(alert.stat().st_mode & 0o777, 0o644)

            MODULE.clear_alert(alert)
            self.assertFalse(alert.exists())


if __name__ == "__main__":
    unittest.main()
