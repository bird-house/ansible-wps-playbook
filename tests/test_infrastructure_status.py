from __future__ import annotations

import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "roles"
    / "wps_tools"
    / "files"
    / "infrastructure-status.py"
)
SPEC = importlib.util.spec_from_file_location("infrastructure_status", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class InfrastructureStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.csv_dir = self.root / "csv"
        self.proc_root = self.root / "proc"
        self.proc_root.mkdir()
        self.now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary.cleanup()

    def write_metric(self, plugin, metric, header, row):
        directory = self.csv_dir / plugin
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{metric}-{self.now.date().isoformat()}"
        path.write_text(f"{header}\n{row}\n", encoding="utf-8")

    def write_proc(self):
        (self.proc_root / "loadavg").write_text(
            "9.00 8.00 7.00 1/100 123\n",
            encoding="utf-8",
        )
        (self.proc_root / "meminfo").write_text(
            "MemTotal:       16777216 kB\n"
            "MemAvailable:   12582912 kB\n",
            encoding="utf-8",
        )

    def test_snapshot_uses_collectd_load_and_memory(self):
        timestamp = int((self.now - timedelta(seconds=30)).timestamp())
        self.write_metric(
            "load",
            "load",
            "epoch,shortterm,midterm,longterm",
            f"{timestamp},1.25,0.75,0.50",
        )
        self.write_metric(
            "memory",
            "memory-used",
            "epoch,value",
            f"{timestamp},{4 * 1024**3}",
        )
        self.write_proc()

        report = MODULE.snapshot(
            csv_dir=self.csv_dir,
            proc_root=self.proc_root,
            filesystems=[self.root],
            collectd_interval=60,
            now=self.now,
        )

        self.assertEqual(report["load"], (1.25, 0.75, 0.5))
        self.assertEqual(report["memory"]["used"], 4 * 1024**3)
        self.assertEqual(report["memory"]["total"], 16 * 1024**3)
        self.assertEqual(report["memory"]["percent"], 25)
        self.assertTrue(report["collectd"]["fresh"])
        self.assertEqual(len(report["filesystems"]), 1)

    def test_snapshot_falls_back_when_collectd_data_is_missing(self):
        self.write_proc()

        report = MODULE.snapshot(
            csv_dir=self.csv_dir,
            proc_root=self.proc_root,
            filesystems=[self.root],
            collectd_interval=60,
            now=self.now,
        )

        self.assertEqual(report["load"], (9.0, 8.0, 7.0))
        self.assertEqual(report["memory"]["used"], 4 * 1024**3)
        self.assertIsNone(report["collectd"]["latest"])

    def test_report_is_compact(self):
        disk = shutil.disk_usage(self.root)
        report = {
            "generated_at": self.now,
            "hostname": "rook7",
            "collectd": {
                "latest": self.now - timedelta(seconds=20),
                "age_seconds": 20,
                "fresh": True,
            },
            "cpu_count": 8,
            "load": (1.0, 0.8, 0.5),
            "memory": {
                "used": 4 * 1024**3,
                "total": 16 * 1024**3,
                "available": 12 * 1024**3,
                "percent": 25.0,
            },
            "filesystems": [
                {
                    "path": "/",
                    "used": disk.used,
                    "free": disk.free,
                    "total": disk.total,
                    "percent": disk.used / disk.total * 100,
                }
            ],
        }
        output = io.StringIO()

        with redirect_stdout(output):
            MODULE.print_report(report)

        text = output.getvalue()
        self.assertIn("Infrastructure — rook7", text)
        self.assertIn("Collectd: fresh", text)
        self.assertIn("Load: 1m=1.0  5m=0.8  15m=0.5  cores=8", text)
        self.assertIn("Memory: used=4.0G/16.0G (25.0%)", text)
        self.assertIn("Filesystems", text)


if __name__ == "__main__":
    unittest.main()
