from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "slurm-job-status.py"
SPEC = importlib.util.spec_from_file_location("slurm_job_status", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SlurmJobStatusTests(unittest.TestCase):
    def test_parse_memory_supports_slurm_units_and_markers(self):
        self.assertEqual(MODULE.parse_memory("512"), 512 * 1024**2)
        self.assertEqual(MODULE.parse_memory("1024K"), 1024**2)
        self.assertEqual(MODULE.parse_memory("1.5G"), int(1.5 * 1024**3))
        self.assertEqual(MODULE.parse_memory("2Gn"), 2 * 1024**3)
        self.assertIsNone(MODULE.parse_memory("N/A"))

    def test_list_jobs_can_scope_squeue_to_user(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="101|fast|R|01:30|01:30:00|4G\n"
            )
        )

        jobs = MODULE.list_jobs("wps", runner)

        self.assertEqual(
            jobs,
            [MODULE.Job("101", "fast", "R", "01:30", "01:30:00", "4G")],
        )
        command = runner.call_args.args[0]
        self.assertIn("--array", command)
        self.assertEqual(command[command.index("--user") + 1], "wps")
        self.assertEqual(command[command.index("--states") + 1], "PENDING,RUNNING")

    def test_batch_max_rss_uses_one_sstat_call(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="101.batch|1536M|\n102.batch|2G|\n"
            )
        )

        usage = MODULE.batch_max_rss(["101", "102"], runner)

        self.assertEqual(usage, {"101": 1536 * 1024**2, "102": 2 * 1024**3})
        command = runner.call_args.args[0]
        self.assertEqual(command[command.index("--jobs") + 1], "101.batch,102.batch")
        self.assertEqual(command[command.index("--format") + 1], "JobID,MaxRSS")
        self.assertEqual(runner.call_count, 1)

    def test_batch_max_rss_tolerates_jobs_finishing_between_queries(self):
        runner = mock.Mock(
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="gone")
        )

        self.assertEqual(MODULE.batch_max_rss(["101"], runner), {"101": None})

    def test_render_includes_pending_jobs_and_memory_percentage(self):
        jobs = [
            MODULE.Job("101", "fast", "R", "01:30", "01:00:00", "4G"),
            MODULE.Job("102", "fast", "PD", "0:00", "01:00:00", "4G"),
        ]

        output = MODULE.render(jobs, {"101": 2 * 1024**3})

        self.assertIn("MAX RSS", output)
        self.assertRegex(output, r"101\s+fast\s+R\s+01:30\s+01:00:00\s+2.0G\s+4.0G\s+50%")
        self.assertRegex(output, r"102\s+fast\s+PD\s+0:00\s+01:00:00\s+-\s+4.0G\s+-")


if __name__ == "__main__":
    unittest.main()
