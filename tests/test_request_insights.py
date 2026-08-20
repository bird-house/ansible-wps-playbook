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
        self.assertIn("3", report["failure_messages"][0]["example_jobs"])
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
        report = MODULE.aggregate([item], top=10)
        coverage = report["coverage"]
        self.assertEqual(
            coverage["orchestrate.workflow.inputs.tas"]["top_values"][0]["value"],
            '"c3s-cordex.output.EUR-11.example.day.tas"',
        )
        self.assertEqual(
            coverage["orchestrate.workflow.steps.subset.time"]["top_values"][0]["value"],
            '"2060/2070"',
        )
        self.assertNotIn("orchestrate.workflow", coverage)
        production = report["orchestrate"]
        collection = "c3s-cordex.output.EUR-11.example.day.tas"
        self.assertEqual(production["jobs_with_workflow_lineage"], 1)
        self.assertEqual(production["jobs_without_workflow_lineage"], 0)
        self.assertEqual(production["collections"][collection]["requests"], 1)
        self.assertEqual(production["collections"][collection]["year_coverage"], "2060-2070")

    def test_orchestrate_failure_is_associated_with_collection(self):
        workflow = {
            "inputs": {"pr": ["c3s-cordex.output.EUR-11.example.day.pr"]},
            "steps": {
                "subset_pr_1": {
                    "run": "subset",
                    "in": {
                        "collection": "inputs/pr",
                        "time": "2056/2070",
                        "time_components": "month:jan,feb|year:2056,2058,2070",
                    },
                }
            },
        }
        item = record("1", "failed", "Job cancelled due to time limit")
        item["process"] = "orchestrate"
        item["inputs"] = {
            "workflow": [{"type": "ComplexData", "value": json.dumps(workflow)}]
        }
        production = MODULE.aggregate([item], top=10)["orchestrate"]
        collection = "c3s-cordex.output.EUR-11.example.day.pr"
        self.assertEqual(
            production["collections"][collection]["year_coverage"],
            "2056,2058,2070",
        )
        self.assertEqual(
            production["collections"][collection]["time_ranges"],
            [{"count": 1, "value": "2056/2070"}],
        )
        self.assertEqual(production["failures"][0]["collection"], collection)
        self.assertEqual(production["failures"][0]["category"], "timeout")
        self.assertEqual(production["failure_categories"], {"timeout": 1})
        self.assertEqual(production["failure_group_count"], 1)
        self.assertEqual(production["failures"][0]["years"], "2056,2058,2070")
        self.assertEqual(production["failures"][0]["time_ranges"], ["2056/2070"])

    def test_derived_step_outputs_are_not_reported_as_collections(self):
        workflow = {
            "inputs": {
                "tas": ["c3s-cmip6.example.tas"],
                "derived": ["regrid_tas_1/output"],
            },
            "steps": {
                "subset_tas_1": {
                    "run": "subset",
                    "in": {"collection": "inputs/tas", "time": "2050/2050"},
                },
                "average_tas_1": {
                    "run": "average",
                    "in": {"collection": "subset_tas_1/output"},
                },
            },
        }
        item = record("1")
        item["process"] = "orchestrate"
        item["inputs"] = {
            "workflow": [{"type": "ComplexData", "value": json.dumps(workflow)}]
        }
        collections = MODULE.aggregate([item], top=10)["orchestrate"]["collections"]
        self.assertEqual(list(collections), ["c3s-cmip6.example.tas"])

    def test_traceback_is_reduced_to_no_data_root_cause(self):
        message = (
            'Traceback File "subset.py" ValueError: There were no valid data points '
            "found in the requested subset. Please expand the area covered by the "
            "bounding box, the time period or the level range you have selected. "
            "During handling of the above exception, another exception occurred: "
            'Traceback File "wps.py" ProcessError: There were no valid data points '
            "found in the requested subset."
        )
        item = record("1", "failed", message)
        item["process"] = "orchestrate"
        report = MODULE.aggregate([item], top=10)
        self.assertEqual(report["failure_categories"], {"no-data": 1})
        self.assertEqual(
            MODULE.primary_failure_message(item),
            "There were no valid data points found in the requested subset. Please "
            "expand the area covered by the bounding box, the time period or the "
            "level range you have selected.",
        )

    def test_process_error_unknown_is_unknown(self):
        item = record("1", "failed", "Process error: unknown")
        self.assertEqual(MODULE.failure_category(item), "unknown")

    def test_scheduler_timeout_discards_preceding_warning(self):
        item = record(
            "1",
            "failed",
            "FutureWarning: xarray defaults will change slurmstepd: error: *** JOB "
            "2867399 ON localhost CANCELLED AT 2026-08-12T19:59:13 DUE TO TIME LIMIT ***",
        )
        self.assertEqual(MODULE.failure_category(item), "timeout")
        self.assertEqual(
            MODULE.primary_failure_message(item),
            "slurmstepd: error: *** JOB 2867399 ON localhost CANCELLED AT "
            "2026-08-12T19:59:13 DUE TO TIME LIMIT ***",
        )

    def test_longitude_domain_failure_is_spatial(self):
        item = record(
            "1",
            "failed",
            "The requested longitude subset -0.37, 1.63 is not within the longitude "
            "bounds of this dataset and could not be converted to this longitude frame.",
        )
        self.assertEqual(MODULE.failure_category(item), "spatial")

    def test_runtime_diagnostic_overrides_generic_xml_failure_category(self):
        item = record("1", "failed", "Process failed, please check server error log")
        item["diagnostics"] = [
            {
                "source": "job-error.txt",
                "message": "slurmstepd: error: Detected 1 oom-kill event(s)",
            }
        ]
        report = MODULE.aggregate([item], top=10)
        self.assertEqual(report["failure_categories"], {"memory": 1})
        self.assertTrue(
            any(
                message["code"] == "diagnostic"
                for message in report["failure_messages"]
            )
        )

    def test_generic_xml_failure_is_unknown_without_diagnostic(self):
        item = record("1", "failed", "Process failed, please check server error log")
        self.assertEqual(MODULE.failure_category(item), "unknown")

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
                    [
                        str(log),
                        "--from",
                        "2026-08-02",
                        "--to",
                        "2026-08-02",
                        "--all-processes",
                        "--json",
                    ]
                )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["requests"], 1)

    def test_defaults_to_orchestrate_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "requests.log"
            subset = record("1")
            orchestrate = record("2")
            orchestrate["process"] = "orchestrate"
            log.write_text(
                json.dumps(subset) + "\n" + json.dumps(orchestrate) + "\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = MODULE.main([str(log), "--json"])
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["requests"], 1)
        self.assertEqual(list(report["processes"]), ["orchestrate"])

    def test_orchestrate_text_omits_duplicate_generic_sections(self):
        report = MODULE.aggregate([dict(record("1"), process="orchestrate")], top=10)
        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.print_report(report)
        self.assertIn("PyWPS request insights", output.getvalue())
        self.assertIn("Orchestrate production data", output.getvalue())
        self.assertNotIn("Requested-data coverage", output.getvalue())
        self.assertNotIn("\nFailure causes", output.getvalue())

    def test_text_uses_compact_timestamps_and_outcomes(self):
        item = dict(record("1"), process="orchestrate")
        item["finished_at"] = "2026-08-01T10:00:00.123456+00:00"
        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.print_report(MODULE.aggregate([item], top=10))
        text = output.getvalue()
        self.assertIn(
            "Period: 2026-08-01T10:00:00+00:00 "
            "to 2026-08-01T10:00:00+00:00",
            text,
        )
        self.assertIn("orchestrate: requests=1 success=1 failures=0", text)
        self.assertNotIn("outcomes=", text)

    def test_orchestrate_text_uses_one_line_per_collection(self):
        workflow = {
            "inputs": {"uas": ["c3s-cmip6.example.uas"]},
            "steps": {
                "subset": {
                    "run": "subset",
                    "in": {"collection": "inputs/uas", "time": "1980/2014"},
                }
            },
        }
        item = record("1")
        item["process"] = "orchestrate"
        item["inputs"] = {
            "workflow": [{"type": "ComplexData", "value": json.dumps(workflow)}]
        }
        report = MODULE.aggregate([item], top=10)
        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.print_report(report)
        lines = output.getvalue().splitlines()
        self.assertEqual(
            [line for line in lines if "c3s-cmip6.example.uas" in line],
            [
                "  c3s-cmip6.example.uas: requests=1 "
                "success=1 failures=0 years=1980-2014"
            ],
        )
        self.assertNotIn("time=1980/2014", output.getvalue())

    def test_orchestrate_failure_categories_are_not_limited_by_top(self):
        records = []
        for number, message in enumerate(
            (
                "There were no valid data points found in the requested subset.",
                "Job cancelled due to time limit",
                "Detected 1 oom-kill event(s)",
            ),
            1,
        ):
            item = record(str(number), "failed", message)
            item["process"] = "orchestrate"
            records.append(item)
        production = MODULE.aggregate(records, top=1)["orchestrate"]
        self.assertEqual(
            production["failure_categories"],
            {"no-data": 1, "timeout": 1, "memory": 1},
        )
        self.assertEqual(production["failure_group_count"], 3)
        self.assertEqual(len(production["failures"]), 1)

    def test_orchestrate_failure_overview_precedes_collections(self):
        workflow = {
            "inputs": {"tas": ["c3s-cmip6.example.tas"]},
            "steps": {
                "subset": {
                    "run": "subset",
                    "in": {"collection": "inputs/tas", "time": "2050/2050"},
                }
            },
        }
        item = record("1", "failed", "Job cancelled due to time limit")
        item["process"] = "orchestrate"
        item["inputs"] = {
            "workflow": [{"type": "ComplexData", "value": json.dumps(workflow)}]
        }
        output = io.StringIO()
        with redirect_stdout(output):
            MODULE.print_report(MODULE.aggregate([item], top=10))
        text = output.getvalue()
        self.assertLess(text.index("Failure causes"), text.index("c3s-cmip6.example.tas"))
        self.assertLess(text.index("c3s-cmip6.example.tas"), text.index("Failed data"))

    def test_collection_sorting_by_name_and_frequency(self):
        records = []
        for number, collection, outcome in (
            (1, "z.collection", "successful"),
            (2, "a.collection", "successful"),
            (3, "z.collection", "failed"),
            (4, "z.collection", "successful"),
        ):
            workflow = {
                "inputs": {"tas": [collection]},
                "steps": {
                    "subset": {
                        "run": "subset",
                        "in": {"collection": "inputs/tas", "time": "2050/2050"},
                    }
                },
            }
            item = record(str(number), outcome, "timeout" if outcome == "failed" else None)
            item["process"] = "orchestrate"
            item["inputs"] = {
                "workflow": [{"type": "ComplexData", "value": json.dumps(workflow)}]
            }
            records.append(item)
        by_name = MODULE.aggregate(records, top=10, sort_by="name")["orchestrate"]
        by_requests = MODULE.aggregate(records, top=10, sort_by="requests")["orchestrate"]
        by_failed = MODULE.aggregate(records, top=10, sort_by="failed")["orchestrate"]
        self.assertEqual(list(by_name["collections"]), ["a.collection", "z.collection"])
        self.assertEqual(list(by_requests["collections"]), ["z.collection", "a.collection"])
        self.assertEqual(list(by_failed["collections"]), ["z.collection", "a.collection"])


if __name__ == "__main__":
    unittest.main()
