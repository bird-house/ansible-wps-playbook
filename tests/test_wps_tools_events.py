from __future__ import annotations

import gzip
import importlib.util
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "roles"
    / "wps_tools"
    / "files"
    / "wps_tools_events.py"
)
SPEC = importlib.util.spec_from_file_location("wps_tools_events_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class WpsToolsEventsTests(unittest.TestCase):
    def test_appends_schema_and_timestamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            MODULE.append_events(path, [{"record_type": "request", "job_id": "one"}])
            record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], 1)
        self.assertIn("recorded_at", record)

    def test_handler_structures_recovery_and_long_running_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            handler = MODULE.JsonlEventHandler(path, "rook", "job-control")
            logger = logging.Logger("event-test")
            logger.addHandler(handler)
            logger.warning("layer=database job=abc status=started finding=long-running age=12m")
            logger.info("layer=xml job=def status=failed action=recovered")
            records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(records[0]["event"], "job-long-running")
        self.assertEqual(records[0]["job_id"], "abc")
        self.assertEqual(records[0]["fields"]["layer"], "database")
        self.assertEqual(records[1]["event"], "job-recovered")

    def test_reads_compressed_json_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl.gz"
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                stream.write('{"event":"example"}\n')
            with MODULE.open_jsonl(path) as stream:
                record = json.loads(stream.readline())
        self.assertEqual(record["event"], "example")


if __name__ == "__main__":
    unittest.main()
