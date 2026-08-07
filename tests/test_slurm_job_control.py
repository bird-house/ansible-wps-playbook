from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "slurm-job-control.py"
SPEC = importlib.util.spec_from_file_location("slurm_job_control", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SlurmJobControlTests(unittest.TestCase):
    def test_parse_elapsed_supports_slurm_duration_formats(self):
        self.assertEqual(MODULE.parse_elapsed("05:30"), 330)
        self.assertEqual(MODULE.parse_elapsed("02:05:30"), 7530)
        self.assertEqual(MODULE.parse_elapsed("2-02:05:30"), 180330)

    def test_list_running_jobs_scopes_squeue_to_user(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="101|RUNNING|01:30:00\n102_4|RUNNING|2-00:00:01\n"
            )
        )

        jobs = MODULE.list_running_jobs("wps", runner)

        self.assertEqual(
            jobs,
            [
                MODULE.SlurmJob("101", "RUNNING", 5400),
                MODULE.SlurmJob("102_4", "RUNNING", 172801),
            ],
        )
        command = runner.call_args.args[0]
        self.assertEqual(command[0], "squeue")
        self.assertEqual(command[command.index("--user") + 1], "wps")
        self.assertEqual(command[command.index("--states") + 1], "RUNNING")

    def test_monitor_reports_only_jobs_at_or_over_timeout(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="100|RUNNING|05:59:59\n101|RUNNING|06:00:00\n"
            )
        )
        logger = mock.Mock()

        summary = MODULE.control_jobs("monitor", "wps", 21600, None, logger, runner)

        self.assertEqual((summary.checked, summary.overdue, summary.cancelled), (2, 1, 0))
        self.assertEqual(runner.call_count, 1)
        logger.warning.assert_called_once()

    def test_recover_cancels_only_overdue_running_jobs(self):
        responses = [
            subprocess.CompletedProcess(
                [], 0, stdout="100|RUNNING|01:00:00\n101|RUNNING|07:00:00\n"
            ),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
        runner = mock.Mock(side_effect=responses)

        summary = MODULE.control_jobs("recover", "wps", 21600, 100, mock.Mock(), runner)

        self.assertEqual((summary.checked, summary.overdue, summary.cancelled), (2, 1, 1))
        self.assertEqual(runner.call_args_list[1].args[0], ["scancel", "101"])

    def test_recovery_limit_bounds_cancellations(self):
        responses = [
            subprocess.CompletedProcess(
                [], 0, stdout="100|RUNNING|07:00:00\n101|RUNNING|08:00:00\n"
            ),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
        runner = mock.Mock(side_effect=responses)

        summary = MODULE.control_jobs("recover", "wps", 21600, 1, mock.Mock(), runner)

        self.assertEqual((summary.checked, summary.overdue, summary.cancelled), (1, 1, 1))
        self.assertEqual(runner.call_count, 2)

    def test_scancel_error_is_reported_and_processing_continues(self):
        responses = [
            subprocess.CompletedProcess(
                [], 0, stdout="100|RUNNING|07:00:00\n101|RUNNING|08:00:00\n"
            ),
            subprocess.CalledProcessError(1, ["scancel", "100"], stderr="invalid job"),
            subprocess.CompletedProcess([], 0, stdout=""),
        ]
        runner = mock.Mock(side_effect=responses)

        summary = MODULE.control_jobs("recover", "wps", 21600, None, mock.Mock(), runner)

        self.assertEqual((summary.overdue, summary.cancelled, summary.errors), (2, 1, 1))

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
