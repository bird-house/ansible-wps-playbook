from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "recover-stalled-jobs.py"
SPEC = importlib.util.spec_from_file_location("recover_stalled_jobs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

WPS = "http://www.opengis.net/wps/1.0.0"
OWS = "http://www.opengis.net/ows/1.1"
JOB_UUID = "123e4567-e89b-42d3-a456-426614174000"


def status_xml(state: str = "ProcessStarted", body: str = "Working") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wps:ExecuteResponse xmlns:wps="{WPS}" xmlns:ows="{OWS}">
  <wps:Status creationTime="2026-07-30T00:00:00Z">
    <wps:{state} percentCompleted="10">{body}</wps:{state}>
  </wps:Status>
</wps:ExecuteResponse>
"""


class RecoverStalledJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.outputs = self.root / "outputs"
        self.work = self.root / "tmp"
        self.outputs.mkdir()
        self.work.mkdir()
        self.status = self.outputs / f"{JOB_UUID}.xml"
        self.status.write_text(status_xml(), encoding="utf-8")
        old = time.time() - 48 * 3600
        os.utime(self.status, (old, old))

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, **overrides):
        values = {
            "output_dir": self.outputs,
            "work_dir": self.work,
            "service_log": None,
            "age_hours": 24.0,
            "recover": False,
            "terminate": False,
            "cleanup": False,
            "slurm": False,
            "user": "wps",
            "squeue_command": "squeue",
            "scancel_command": "scancel",
            "term_timeout": 0.0,
            "lock_file": self.root / "lock",
            "log_file": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_detects_old_pending_status_and_preserves_namespace_on_update(self):
        document = MODULE.parse_status(self.status, time.time(), self.work)
        self.assertEqual(document.state, "ProcessStarted")
        self.assertGreater(document.age_seconds, 47 * 3600)
        MODULE.update_failed(document, "stalled")
        root = ET.parse(self.status).getroot()
        failed = root.find(f".//{{{WPS}}}ProcessFailed")
        self.assertIsNotNone(failed)
        exception = failed.find(f".//{{{OWS}}}Exception")
        self.assertIsNotNone(exception)
        self.assertEqual(exception.attrib["exceptionCode"], "NoApplicableCode")
        self.assertEqual(
            failed.find(f".//{{{OWS}}}ExceptionText").text,
            "stalled",
        )
        self.assertIn("xmlns:wps=", self.status.read_text(encoding="utf-8"))
        self.assertIn("xmlns:ows=", self.status.read_text(encoding="utf-8"))

    def test_terminal_and_recent_statuses_are_not_candidates(self):
        self.status.write_text(status_xml("ProcessSucceeded", "done"), encoding="utf-8")
        terminal = MODULE.parse_status(self.status, time.time(), self.work)
        self.assertIn(terminal.state, MODULE.TERMINAL_STATES)
        self.status.write_text(status_xml(), encoding="utf-8")
        recent = MODULE.parse_status(self.status, time.time(), self.work)
        self.assertLess(recent.age_seconds, 60)

    def test_report_mode_does_not_change_status(self):
        before = self.status.read_bytes()
        with mock.patch.object(MODULE.pwd, "getpwnam", return_value=mock.Mock(pw_uid=os.getuid())):
            result = MODULE.run(self.args(), mock.Mock(), now=time.time())
        self.assertEqual(result, 0)
        self.assertEqual(self.status.read_bytes(), before)

    def test_recovery_skips_a_live_logged_process_without_terminate(self):
        log = self.root / "service.log"
        log.write_text(
            f"Started processing request: {JOB_UUID} with pid: {os.getpid()}\n",
            encoding="utf-8",
        )
        with mock.patch.object(MODULE.pwd, "getpwnam", return_value=mock.Mock(pw_uid=os.getuid())):
            result = MODULE.run(
                self.args(recover=True, service_log=log),
                mock.Mock(),
                now=time.time(),
            )
        self.assertEqual(result, 0)
        self.assertIn("ProcessStarted", self.status.read_text(encoding="utf-8"))

    def test_recovery_updates_status_and_removes_associated_resource(self):
        resource = self.work / f"pywps_process_{JOB_UUID}"
        resource.mkdir()
        payload = resource / "result.nc"
        payload.write_text("partial", encoding="utf-8")
        self.status.write_text(status_xml(body=str(payload)), encoding="utf-8")
        log = self.root / "service.log"
        log.write_text(
            f"Started processing request: {JOB_UUID} with pid: 99999999\n",
            encoding="utf-8",
        )
        old = time.time() - 48 * 3600
        os.utime(self.status, (old, old))
        with mock.patch.object(MODULE.pwd, "getpwnam", return_value=mock.Mock(pw_uid=os.getuid())):
            result = MODULE.run(
                self.args(recover=True, cleanup=True, service_log=log),
                mock.Mock(),
                now=time.time(),
            )
        self.assertEqual(result, 0)
        self.assertFalse(resource.exists())
        self.assertIn("ProcessFailed", self.status.read_text(encoding="utf-8"))

    def test_accepted_job_without_pid_evidence_is_not_recovered(self):
        self.status.write_text(status_xml("ProcessAccepted", "queued"), encoding="utf-8")
        old = time.time() - 48 * 3600
        os.utime(self.status, (old, old))
        with mock.patch.object(MODULE.pwd, "getpwnam", return_value=mock.Mock(pw_uid=os.getuid())):
            result = MODULE.run(self.args(recover=True), mock.Mock(), now=time.time())
        self.assertEqual(result, 0)
        self.assertIn("ProcessAccepted", self.status.read_text(encoding="utf-8"))

    def test_malformed_xml_is_reported_without_replacement(self):
        self.status.write_text("<broken", encoding="utf-8")
        before = self.status.read_bytes()
        with mock.patch.object(MODULE.pwd, "getpwnam", return_value=mock.Mock(pw_uid=os.getuid())):
            result = MODULE.run(self.args(recover=True), mock.Mock(), now=time.time())
        self.assertEqual(result, 1)
        self.assertEqual(self.status.read_bytes(), before)

    def test_changed_file_is_not_replaced(self):
        document = MODULE.parse_status(self.status, time.time(), self.work)
        self.status.write_text(status_xml(body="new progress"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed during inspection"):
            MODULE.update_failed(document, "stalled")
        self.assertIn("new progress", self.status.read_text(encoding="utf-8"))

    def test_cleanup_refuses_paths_outside_work_directory(self):
        outside = self.root / "pywps_process_outside"
        outside.mkdir()
        with self.assertRaisesRegex(RuntimeError, "outside"):
            MODULE.remove_resources([outside], self.work, mock.Mock())
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
