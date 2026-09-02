from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "roles"
    / "wps_tools"
    / "files"
    / "pywps-temp-cleanup.py"
)
SPEC = importlib.util.spec_from_file_location("pywps_temp_cleanup", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

UTC = timezone.utc
ACTIVE_UUID = "123e4567-e89b-42d3-a456-426614174000"
FINAL_UUID = "223e4567-e89b-42d3-a456-426614174000"
ORPHAN_UUID = "323e4567-e89b-42d3-a456-426614174000"


class TempCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.work_dir = self.root / "work"
        self.work_dir.mkdir()
        self.now = datetime(2026, 9, 2, 12, tzinfo=UTC)

    def tearDown(self):
        self.temporary.cleanup()

    def work_directory(
        self,
        name: str,
        job_uuid: str | None,
        age: timedelta = timedelta(hours=4),
    ) -> Path:
        directory = self.work_dir / f"pywps_process_{name}"
        directory.mkdir()
        if job_uuid is not None:
            (directory / "job_test.dump").write_text(
                json.dumps({"process": {"uuid": job_uuid}}),
                encoding="utf-8",
            )
        timestamp = (self.now - age).timestamp()
        os.utime(directory, (timestamp, timestamp))
        return directory

    def test_dump_uuid_is_validated(self):
        directory = self.work_directory("valid", ACTIVE_UUID)
        self.assertEqual(MODULE.job_uuid_from_dump(directory), ACTIVE_UUID)

        invalid = self.work_directory("invalid", "not-a-uuid")
        with self.assertRaisesRegex(ValueError, "invalid job UUID"):
            MODULE.job_uuid_from_dump(invalid)

    def test_cleanup_protects_nonfinal_and_removes_final_or_orphaned_jobs(self):
        active = self.work_directory("active", ACTIVE_UUID)
        final = self.work_directory("final", FINAL_UUID)
        orphan = self.work_directory("orphan", ORPHAN_UUID)
        recent = self.work_directory("recent", FINAL_UUID, timedelta(hours=1))

        with redirect_stderr(io.StringIO()):
            jobs, summary = MODULE.discover_aged_jobs(
                self.work_dir,
                self.now - timedelta(hours=3),
            )
        MODULE.remove_safe_jobs(
            jobs,
            {ACTIVE_UUID: False, FINAL_UUID: True},
            summary,
        )

        self.assertTrue(active.exists())
        self.assertFalse(final.exists())
        self.assertFalse(orphan.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(
            (
                summary.checked,
                summary.deleted,
                summary.protected,
                summary.skipped,
                summary.errors,
            ),
            (3, 2, 1, 0, 0),
        )

    def test_cleanup_keeps_unassociated_and_unsafe_directories(self):
        unassociated = self.work_directory("unknown", None)
        target = self.root / "outside"
        target.mkdir()
        linked = self.work_dir / "pywps_process_linked"
        linked.symlink_to(target, target_is_directory=True)

        with redirect_stderr(io.StringIO()):
            jobs, summary = MODULE.discover_aged_jobs(
                self.work_dir,
                self.now - timedelta(hours=3),
            )

        self.assertEqual(jobs, [])
        self.assertTrue(unassociated.exists())
        self.assertTrue(linked.is_symlink())
        self.assertTrue(target.exists())
        self.assertEqual((summary.skipped, summary.errors), (2, 2))

    def test_cleanup_refuses_directory_changed_after_database_check(self):
        directory = self.work_directory("changed", FINAL_UUID)
        jobs, summary = MODULE.discover_aged_jobs(
            self.work_dir,
            self.now - timedelta(hours=3),
        )
        (directory / "changed-after-scan").touch()

        with redirect_stderr(io.StringIO()):
            MODULE.remove_safe_jobs(jobs, {FINAL_UUID: True}, summary)

        self.assertTrue(directory.exists())
        self.assertEqual((summary.deleted, summary.skipped, summary.errors), (0, 1, 1))


if __name__ == "__main__":
    unittest.main()
