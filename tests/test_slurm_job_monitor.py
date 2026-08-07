from __future__ import annotations

import importlib.util
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

    def test_pending_queue_warning_uses_configured_threshold(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="100|PENDING|0:00\n101|PENDING|0:00\n"
            )
        )
        logger = mock.Mock()

        summary = MODULE.inspect_jobs("wps", 14400, 2, logger, runner)

        self.assertEqual((summary.running, summary.pending), (0, 2))
        logger.warning.assert_called_once_with(
            "finding=pending-queue-full pending=%d warning_threshold=%d",
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
        self.assertEqual(handlers[0].level, MODULE.logging.WARNING)
        self.assertEqual(handlers[1].level, MODULE.logging.INFO)
        handlers[1].close()


if __name__ == "__main__":
    unittest.main()
