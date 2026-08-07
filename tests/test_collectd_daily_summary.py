import gzip
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "collectd-daily-summary"
SUMMARY_DATE = "2026-08-06"
GIB = 1024**3


class CollectdDailySummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.csv_dir = self.root / "csv" / "localhost"
        self.csv_dir.mkdir(parents=True)
        self.output = self.root / "summary.log"

    def tearDown(self):
        self.temporary.cleanup()

    def write_metric(self, plugin, metric, header, rows, compressed=False):
        directory = self.csv_dir / plugin
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{metric}-{SUMMARY_DATE}"
        content = "\n".join((header, *rows)) + "\n"
        if compressed:
            with gzip.open(f"{path}.gz", "wt", encoding="utf-8") as target:
                target.write(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def run_script(self, *metrics):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--csv-dir",
                str(self.csv_dir),
                "--output",
                str(self.output),
                "--date",
                SUMMARY_DATE,
                "--lock-file",
                str(self.root / "summary.lock"),
                *metrics,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def write_load(self, values, compressed=False):
        return self.write_metric(
            "load",
            "load",
            "epoch,shortterm,midterm,longterm",
            [f"{index},{value},0,0" for index, value in enumerate(values)],
            compressed=compressed,
        )

    def test_summarizes_all_enabled_metrics(self):
        self.write_load((0.5, 2.25))
        self.write_metric(
            "df-mnt-ext_pywps_outputs",
            "percent_bytes-used",
            "epoch,value",
            ("0,20", "1,75.5"),
        )
        self.write_metric(
            "memory",
            "memory-used",
            "epoch,value",
            (f"0,{GIB}", f"1,{2 * GIB}"),
        )
        self.write_metric(
            "interface-ens3",
            "if_octets",
            "epoch,rx,tx",
            (f"0,0,0", f"1,{GIB},0", f"2,{2 * GIB},0"),
        )

        result = self.run_script(
            "--load",
            "--disk-mount",
            "/mnt/ext_pywps_outputs",
            "--memory",
            "--interface",
            "ens3",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.output.read_text(encoding="utf-8")
        self.assertIn("load_min=0.5  load_max=2.25", summary)
        self.assertIn("disk_min=20.0  disk_max=75.5", summary)
        self.assertIn("mem_min=1.0 GiB  mem_max=2.0 GiB", summary)
        self.assertIn("ens3_rx=2.0 GiB", summary)

    def test_omits_disabled_metrics(self):
        self.write_load((1, 3))

        result = self.run_script("--load")

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self.output.read_text(encoding="utf-8")
        self.assertIn("load_min=1.0", summary)
        self.assertNotIn("disk_", summary)
        self.assertNotIn("mem_", summary)
        self.assertNotIn("_rx=", summary)

    def test_replaces_an_existing_summary_for_the_same_date(self):
        self.output.write_text(
            "2026-08-05  load_min=1.0\n"
            f"{SUMMARY_DATE}  load_min=old\n",
            encoding="utf-8",
        )
        self.write_load((2, 4))

        result = self.run_script("--load")

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.output.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(sum(line.startswith(SUMMARY_DATE) for line in lines), 1)
        self.assertIn("load_min=2.0", lines[1])

    def test_reports_missing_enabled_metric_as_na(self):
        result = self.run_script("--load")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("load_min=NA  load_max=NA", self.output.read_text())
        self.assertIn("summary warning: load:", result.stderr)

    def test_reads_compressed_csv_for_manual_backfill(self):
        self.write_load((1.5, 2.5), compressed=True)

        result = self.run_script("--load")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("load_min=1.5  load_max=2.5", self.output.read_text())


if __name__ == "__main__":
    unittest.main()
