from __future__ import annotations

import importlib.util
import fcntl
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "pywps-xml-inspect.py"
SPEC = importlib.util.spec_from_file_location("pywps_xml_inspect", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

JOB_ID = "123e4567-e89b-42d3-a456-426614174000"


def status_xml(state="ProcessSucceeded"):
    failure = ""
    if state == "ProcessFailed":
        failure = """<wps:ExceptionReport><ows:Exception exceptionCode="NoApplicableCode"
          locator="dataset"><ows:ExceptionText>Input was unavailable</ows:ExceptionText>
          </ows:Exception></wps:ExceptionReport>"""
    return f"""<wps:ExecuteResponse xmlns:wps="http://www.opengis.net/wps/1.0.0"
      xmlns:ows="http://www.opengis.net/ows/1.1" xmlns:xlink="http://www.w3.org/1999/xlink">
      <wps:Process><ows:Identifier>subset</ows:Identifier></wps:Process>
      <wps:Status creationTime="2026-08-12T10:00:00Z"><wps:{state}>{failure}</wps:{state}></wps:Status>
      <wps:DataInputs>
        <wps:Input><ows:Identifier>dataset</ows:Identifier><wps:Reference xlink:href="https://data.test/a.nc"/></wps:Input>
        <wps:Input><ows:Identifier>time</ows:Identifier><wps:Data><wps:LiteralData>2020/2021</wps:LiteralData></wps:Data></wps:Input>
      </wps:DataInputs>
    </wps:ExecuteResponse>"""


class XmlInspectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "outputs"
        self.output.mkdir()
        self.status = self.output / f"{JOB_ID}.xml"
        self.finished = datetime(2026, 8, 12, 10, 2, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, state="ProcessSucceeded"):
        self.status.write_text(status_xml(state), encoding="utf-8")
        os.utime(self.status, (self.finished.timestamp(), self.finished.timestamp()))

    def test_extracts_request_duration_and_inputs(self):
        self.write()
        record = MODULE.inspect(self.status, "rook")
        self.assertEqual(record["process"], "subset")
        self.assertEqual(record["outcome"], "successful")
        self.assertEqual(record["duration_seconds"], 120.0)
        self.assertEqual(record["inputs"]["dataset"][0]["value"], "https://data.test/a.nc")
        self.assertEqual(record["inputs"]["time"][0]["value"], "2020/2021")

    def test_extracts_failure_details(self):
        self.write("ProcessFailed")
        record = MODULE.inspect(self.status, "rook")
        self.assertEqual(record["outcome"], "failed")
        self.assertEqual(record["failures"][0]["code"], "NoApplicableCode")
        self.assertEqual(record["failures"][0]["locator"], "dataset")
        self.assertEqual(record["failures"][0]["message"], "Input was unavailable")

    def test_ignores_non_final_status(self):
        self.write("ProcessStarted")
        self.assertIsNone(MODULE.inspect(self.status, "rook"))

    def test_state_suppresses_duplicates(self):
        self.write()
        state = self.root / "state.json"
        arguments = ["--output-dir", str(self.output), "--state-file", str(state)]
        first = io.StringIO()
        with redirect_stdout(first):
            self.assertEqual(MODULE.main(arguments), 0)
        self.assertEqual(json.loads(first.getvalue())["job_id"], JOB_ID)
        second = io.StringIO()
        with redirect_stdout(second):
            self.assertEqual(MODULE.main(arguments), 0)
        self.assertEqual(second.getvalue(), "")

    def test_active_lock_skips_overlapping_scan(self):
        self.write()
        lock_path = self.root / "inspect.lock"
        state = self.root / "state.json"
        with lock_path.open("w", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            output = io.StringIO()
            with redirect_stdout(output):
                result = MODULE.main(
                    [
                        "--output-dir",
                        str(self.output),
                        "--state-file",
                        str(state),
                        "--lock-file",
                        str(lock_path),
                    ]
                )
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(state.exists())

    def test_loads_matching_job_error_diagnostic(self):
        process_dir = self.root / "work" / "pywps_process_subset"
        process_dir.mkdir(parents=True)
        (process_dir / "job_1.dump").write_text(
            json.dumps({"process": {"uuid": JOB_ID}}), encoding="utf-8"
        )
        (process_dir / "job-error.txt").write_text(
            "slurmstepd: error: Detected 1 oom-kill event(s)", encoding="utf-8"
        )
        diagnostics = MODULE.load_job_diagnostics(self.root / "work")
        self.assertEqual(diagnostics[JOB_ID][0]["source"], "job-error.txt")
        self.assertIn("oom-kill", diagnostics[JOB_ID][0]["message"])


if __name__ == "__main__":
    unittest.main()
