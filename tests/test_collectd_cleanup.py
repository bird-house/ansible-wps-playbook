import gzip
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "collectd-cleanup"


class CollectdCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.csv_dir = self.root / "csv" / "localhost" / "load"
        self.csv_dir.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def make_file(self, name, age_days, content=b"value\n"):
        path = self.csv_dir / name
        path.write_bytes(content)
        modified = time.time() - age_days * 86400
        os.utime(path, (modified, modified))
        return path

    def run_script(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--csv-dir",
                str(self.root / "csv" / "localhost"),
                "--lock-file",
                str(self.root / "cleanup.lock"),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_compresses_old_files_and_preserves_recent_files(self):
        recent = self.make_file("load-2026-08-06", 1)
        old = self.make_file("load-2026-07-29", 9, b"old data\n")

        result = self.run_script(
            "--compress-after-days", "7", "--keep-days", "30"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(recent.exists())
        self.assertFalse(old.exists())
        with gzip.open(f"{old}.gz", "rb") as compressed:
            self.assertEqual(compressed.read(), b"old data\n")
        self.assertIn("compressed=1 deleted=0", result.stdout)

    def test_deletes_expired_raw_and_compressed_files(self):
        raw = self.make_file("load-2026-06-01", 31)
        compressed = self.make_file("load-2026-06-02.gz", 31)

        result = self.run_script(
            "--compress-after-days", "7", "--keep-days", "30"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(raw.exists())
        self.assertFalse(compressed.exists())
        self.assertIn("compressed=0 deleted=2", result.stdout)

    def test_leaves_unrelated_files_untouched(self):
        unrelated = self.make_file("notes.txt", 31)

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(unrelated.exists())

    def test_rejects_invalid_retention(self):
        result = self.run_script(
            "--compress-after-days", "30", "--keep-days", "7"
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("must be less than", result.stderr)


if __name__ == "__main__":
    unittest.main()
