from __future__ import annotations

import gzip
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "pywps-request-insights.py"
SPEC = importlib.util.spec_from_file_location("pywps_request_insights", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def record(job, outcome="successful", message=None, dataset="tas", duration=60):
    failures = []
    if message:
        failures.append({"code": "NoApplicableCode", "locator": None, "message": message})
    return {
        "service": "rook",
        "job_id": job,
        "process": "subset",
        "finished_at": f"2026-08-0{job}T10:00:00+00:00",
        "duration_seconds": duration,
        "outcome": outcome,
        "inputs": {"dataset": [{"type": "LiteralData", "value": dataset}]},
        "failures": failures,
    }


class RequestInsightsTests(unittest.TestCase):
    def test_aggregates_coverage_durations_and_failure_causes(self):
        records = [
            record("1", dataset="tas", duration=60),
            record("2", dataset="tas", duration=120),
            record("3", "failed", "slurmstepd: oom-kill event(s)", "pr", 180),
            record("4", "failed", "Job cancelled due to time limit", "tas", 240),
        ]
        report = MODULE.aggregate(records, top=10)
        self.assertEqual(report["requests"], 4)
        self.assertEqual(report["outcomes"], {"failed": 2, "successful": 2})
        self.assertEqual(report["coverage"]["subset.dataset"]["distinct_values"], 2)
        self.assertEqual(report["coverage"]["subset.dataset"]["top_values"][0]["count"], 3)
        self.assertEqual(report["failure_categories"], {"memory": 1, "timeout": 1})
        self.assertEqual(report["durations"]["all"]["max_seconds"], 240.0)
        self.assertEqual(
            MODULE.failure_category(
                record(
                    "5",
                    "failed",
                    "stalled-job recovery found no status update for at least 30 minutes",
                )
            ),
            "timeout",
        )

    def test_reads_gzip_and_deduplicates_rotated_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "requests.log"
            rotated = root / "requests.log.1.gz"
            line = json.dumps(record("1")) + "\n"
            current.write_text(line, encoding="utf-8")
            with gzip.open(rotated, "wt", encoding="utf-8") as stream:
                stream.write(line)
            records, errors = MODULE.load_records([current, rotated])
        self.assertEqual(len(records), 1)
        self.assertEqual(errors, [])

    def test_unpacks_workflow_collections_and_step_parameters(self):
        workflow = {
            "inputs": {"tas": ["c3s-cordex.output.EUR-11.example.day.tas"]},
            "steps": {
                "subset_tas_1": {
                    "run": "subset",
                    "in": {"collection": "inputs/tas", "time": "2060/2070"},
                }
            },
            "outputs": {"output": "subset_tas_1/output"},
        }
        item = record("1")
        item["process"] = "orchestrate"
        item["inputs"] = {
            "workflow": [
                {"type": "ComplexData", "value": json.dumps(workflow)}
            ]
        }
        coverage = MODULE.aggregate([item], top=10)["coverage"]
        self.assertEqual(
            coverage["orchestrate.workflow.inputs.tas"]["top_values"][0]["value"],
            '"c3s-cordex.output.EUR-11.example.day.tas"',
        )
        self.assertEqual(
            coverage["orchestrate.workflow.steps.subset.time"]["top_values"][0]["value"],
            '"2060/2070"',
        )
        self.assertNotIn("orchestrate.workflow", coverage)

    def test_filters_dates_and_renders_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "requests.log"
            log.write_text(
                "\n".join(json.dumps(record(str(day))) for day in (1, 2, 3)) + "\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = MODULE.main(
                    [str(log), "--from", "2026-08-02", "--to", "2026-08-02", "--json"]
                )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["requests"], 1)


if __name__ == "__main__":
    unittest.main()
