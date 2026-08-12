#!/usr/bin/env python3
"""Turn PyWPS request logs into coverage, performance, and failure insights."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Iterable, TextIO


UTC = timezone.utc
FAILURE_PATTERNS = (
    (
        "memory",
        re.compile(
            r"\b(?:oom|oom-kill|memoryerror)\b|out of memory|cannot allocate memory|"
            r"memory (?:limit|allocation)|exceeded[^\n]*memory",
            re.IGNORECASE,
        ),
    ),
    (
        "timeout",
        re.compile(
            r"timed?\s*out|time(?: |-)?limit|deadline exceeded|walltime|wall clock|"
            r"cancelled[^\n]*time|no (?:status|database) update[^\n]*minutes|"
            r"exceeded[^\n]*(?:run|execution) time",
            re.IGNORECASE,
        ),
    ),
    (
        "input",
        re.compile(
            r"invalid (?:input|parameter)|missing (?:input|parameter)|not found|"
            r"unavailable|permission denied|access denied",
            re.IGNORECASE,
        ),
    ),
    (
        "scheduler",
        re.compile(r"\b(?:slurm|drmaa|scheduler|sbatch|srun|squeue)\b", re.IGNORECASE),
    ),
)
GENERIC_FAILURE_RE = re.compile(
    r"^NoApplicableCode\s+(?:None\s+)?Process failed, please check server error log\s*$",
    re.IGNORECASE,
)


def parse_time(value: str, *, end: bool = False) -> datetime:
    text = value.strip()
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO date or timestamp: {value}") from error
    if len(text) == 10:
        parsed = datetime.combine(parsed.date(), time.max if end else time.min)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(UTC)


def open_log(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def load_records(paths: Iterable[Path]) -> tuple[list[dict[str, object]], list[str]]:
    records = []
    errors = []
    seen = set()
    for path in paths:
        try:
            with open_log(path) as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as error:
                        errors.append(f"{path}:{line_number}: invalid JSON: {error.msg}")
                        continue
                    if not isinstance(record, dict):
                        errors.append(f"{path}:{line_number}: record is not an object")
                        continue
                    job_id = record.get("job_id")
                    identity = (
                        (record.get("service"), job_id)
                        if isinstance(job_id, str) and job_id
                        else (str(path), line_number)
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    records.append(record)
        except OSError as error:
            errors.append(f"{path}: {error}")
    return records, errors


def record_time(record: dict[str, object]) -> datetime | None:
    value = record.get("finished_at") or record.get("recorded_at")
    if not isinstance(value, str):
        return None
    try:
        return parse_time(value)
    except argparse.ArgumentTypeError:
        return None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return round(ordered[index], 3)


def duration_summary(values: list[float]) -> dict[str, object]:
    return {
        "count": len(values),
        "median_seconds": round(statistics.median(values), 3) if values else None,
        "p95_seconds": percentile(values, 0.95),
        "max_seconds": round(max(values), 3) if values else None,
    }


def failure_text(record: dict[str, object]) -> str:
    parts = []
    failures = record.get("failures")
    if isinstance(failures, list):
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            parts.extend(str(failure.get(key) or "") for key in ("code", "locator", "message"))
    diagnostics = record.get("diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if isinstance(diagnostic, dict):
                parts.extend(
                    str(diagnostic.get(key) or "") for key in ("source", "message")
                )
    return " ".join(parts)


def failure_category(record: dict[str, object]) -> str:
    text = failure_text(record)
    for name, pattern in FAILURE_PATTERNS:
        if pattern.search(text):
            return name
    if GENERIC_FAILURE_RE.match(text.strip()):
        return "unknown"
    return "other" if text.strip() else "unknown"


def flatten_json(prefix: str, value: object) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        result = []
        for name, child in sorted(value.items()):
            result.extend(flatten_json(f"{prefix}.{name}", child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(flatten_json(prefix, child))
        return result
    return [(prefix, json.dumps(value, ensure_ascii=False, sort_keys=True))]


def workflow_coverage(
    process: str, input_name: str, value: dict[str, object]
) -> list[tuple[str, str]] | None:
    workflow_inputs = value.get("inputs")
    steps = value.get("steps")
    if not isinstance(workflow_inputs, dict) or not isinstance(steps, dict):
        return None
    prefix = f"{process}.{input_name}"
    result = flatten_json(f"{prefix}.inputs", workflow_inputs)
    for step in steps.values():
        if not isinstance(step, dict):
            continue
        operation = str(step.get("run") or "unknown")
        parameters = step.get("in")
        if isinstance(parameters, dict):
            result.extend(flatten_json(f"{prefix}.steps.{operation}", parameters))
    return result


def coverage_items(
    process: str, input_name: str, value: object
) -> list[tuple[str, str]]:
    prefix = f"{process}.{input_name}"
    if isinstance(value, dict) and value.get("type") == "ComplexData":
        contents = value.get("value")
        if isinstance(contents, str):
            try:
                structured = json.loads(contents)
            except json.JSONDecodeError:
                structured = None
            if isinstance(structured, dict):
                workflow = workflow_coverage(process, input_name, structured)
                if workflow is not None:
                    return workflow
                return flatten_json(prefix, structured)
    if isinstance(value, dict):
        rendered = json.dumps(
            {item: value.get(item) for item in ("type", "value")},
            sort_keys=True,
            ensure_ascii=False,
        )
    else:
        rendered = json.dumps(value, ensure_ascii=False)
    return [(prefix, rendered)]


def string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in string_values(child)]
    return []


def requested_years(parameters: dict[str, object]) -> tuple[set[int], list[str]]:
    ranges = string_values(parameters.get("time"))
    component_values = string_values(parameters.get("time_components"))
    component_years = set()
    for value in component_values:
        match = re.search(r"(?:^|\|)year:([^|]+)", value)
        if match:
            component_years.update(int(year) for year in re.findall(r"\b[12][0-9]{3}\b", match.group(1)))
    if component_years:
        return component_years, ranges + component_values
    years = set()
    for value in ranges:
        endpoints = [int(year) for year in re.findall(r"\b[12][0-9]{3}\b", value)]
        if len(endpoints) >= 2:
            years.update(range(min(endpoints), max(endpoints) + 1))
        elif endpoints:
            years.add(endpoints[0])
    return years, ranges


def workflow_payloads(record: dict[str, object]) -> list[dict[str, object]]:
    inputs = record.get("inputs")
    if not isinstance(inputs, dict):
        return []
    workflows = inputs.get("workflow")
    if not isinstance(workflows, list):
        return []
    result = []
    for workflow in workflows:
        if not isinstance(workflow, dict) or workflow.get("type") != "ComplexData":
            continue
        value = workflow.get("value")
        if not isinstance(value, str):
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            result.append(payload)
    return result


def orchestrate_request_data(
    record: dict[str, object],
) -> tuple[dict[str, dict[str, object]], bool]:
    payloads = workflow_payloads(record)
    collections: dict[str, dict[str, object]] = {}
    for payload in payloads:
        workflow_inputs = payload.get("inputs")
        steps = payload.get("steps")
        if not isinstance(workflow_inputs, dict):
            continue
        aliases = {
            str(alias): string_values(value) for alias, value in workflow_inputs.items()
        }
        for values in aliases.values():
            for collection in values:
                collections.setdefault(collection, {"years": set(), "ranges": []})
        if not isinstance(steps, dict):
            continue
        for step in steps.values():
            if not isinstance(step, dict) or not isinstance(step.get("in"), dict):
                continue
            parameters = step["in"]
            years, ranges = requested_years(parameters)
            references = string_values(parameters.get("collection"))
            selected = []
            for reference in references:
                alias = reference.split("/", 1)[1] if reference.startswith("inputs/") else reference
                selected.extend(aliases.get(alias, [reference] if reference not in aliases else []))
            if not selected:
                selected = [item for values in aliases.values() for item in values]
            for collection in selected:
                details = collections.setdefault(collection, {"years": set(), "ranges": []})
                details["years"].update(years)
                details["ranges"].extend(ranges)
    return collections, bool(payloads)


def compact_years(years: set[int]) -> str:
    if not years:
        return "unknown"
    ordered = sorted(years)
    groups = []
    start = previous = ordered[0]
    for year in ordered[1:]:
        if year == previous + 1:
            previous = year
            continue
        groups.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = year
    groups.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(groups)


def primary_failure_message(record: dict[str, object]) -> str:
    diagnostics = record.get("diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if isinstance(diagnostic, dict) and diagnostic.get("message"):
                return str(diagnostic["message"])
    failures = record.get("failures")
    if isinstance(failures, list):
        for failure in failures:
            if isinstance(failure, dict) and failure.get("message"):
                return str(failure["message"])
    return "unknown failure"


def aggregate_orchestrate(
    records: list[dict[str, object]], top: int
) -> dict[str, object] | None:
    orchestrate = [record for record in records if record.get("process") == "orchestrate"]
    if not orchestrate:
        return None
    lineage_jobs = 0
    collection_counts: dict[str, Counter[str]] = defaultdict(Counter)
    collection_years: dict[str, set[int]] = defaultdict(set)
    collection_ranges: dict[str, Counter[str]] = defaultdict(Counter)
    failures: Counter[tuple[str, str, str]] = Counter()
    failure_jobs: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    outcomes: Counter[str] = Counter()
    for record in orchestrate:
        outcome = str(record.get("outcome") or "unknown")
        outcomes[outcome] += 1
        requested, has_lineage = orchestrate_request_data(record)
        lineage_jobs += int(has_lineage)
        targets = list(requested)
        if outcome == "failed" and not targets:
            targets = ["unknown"]
        for collection, details in requested.items():
            collection_counts[collection][outcome] += 1
            collection_years[collection].update(details["years"])
            collection_ranges[collection].update(details["ranges"])
        if outcome == "failed":
            category = failure_category(record)
            message = primary_failure_message(record)
            for collection in targets:
                key = (collection, category, message)
                failures[key] += 1
                failure_jobs[key].add(str(record.get("job_id") or "unknown"))
    collections = {}
    for collection, counts in sorted(collection_counts.items()):
        years = collection_years[collection]
        collections[collection] = {
            "requests": sum(counts.values()),
            "outcomes": dict(sorted(counts.items())),
            "years": sorted(years),
            "year_coverage": compact_years(years),
            "time_ranges": [
                {"count": count, "value": value}
                for value, count in collection_ranges[collection].most_common(top)
            ],
        }
    failure_report = [
        {
            "count": count,
            "collection": key[0],
            "category": key[1],
            "message": key[2],
            "example_jobs": sorted(failure_jobs[key])[:3],
        }
        for key, count in failures.most_common(top)
    ]
    return {
        "requests": len(orchestrate),
        "outcomes": dict(sorted(outcomes.items())),
        "jobs_with_workflow_lineage": lineage_jobs,
        "jobs_without_workflow_lineage": len(orchestrate) - lineage_jobs,
        "collections": collections,
        "failures": failure_report,
    }


def aggregate(records: list[dict[str, object]], top: int) -> dict[str, object]:
    outcomes: Counter[str] = Counter()
    processes: dict[str, Counter[str]] = defaultdict(Counter)
    process_durations: dict[str, list[float]] = defaultdict(list)
    durations: dict[str, list[float]] = defaultdict(list)
    coverage: dict[str, Counter[str]] = defaultdict(Counter)
    failure_categories: Counter[str] = Counter()
    failure_messages: Counter[tuple[str, str, str, str]] = Counter()
    failure_jobs: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    timestamps = []

    for record in records:
        outcome = str(record.get("outcome") or "unknown")
        process = str(record.get("process") or "unknown")
        outcomes[outcome] += 1
        processes[process][outcome] += 1
        timestamp = record_time(record)
        if timestamp:
            timestamps.append(timestamp)
        duration = record.get("duration_seconds")
        if isinstance(duration, (int, float)) and duration >= 0:
            process_durations[process].append(float(duration))
            durations["all"].append(float(duration))
            durations[outcome].append(float(duration))

        inputs = record.get("inputs")
        if isinstance(inputs, dict):
            for name, values in inputs.items():
                if not isinstance(values, list):
                    continue
                for value in values:
                    for key, rendered in coverage_items(process, str(name), value):
                        coverage[key][rendered] += 1

        if outcome == "failed":
            category = failure_category(record)
            failure_categories[category] += 1
            failures = record.get("failures")
            if isinstance(failures, list):
                for failure in failures:
                    if not isinstance(failure, dict):
                        continue
                    key = (
                        category,
                        str(failure.get("code") or ""),
                        str(failure.get("locator") or ""),
                        str(failure.get("message") or "unknown failure"),
                    )
                    failure_messages[key] += 1
                    failure_jobs[key].add(str(record.get("job_id") or "unknown"))
            diagnostics = record.get("diagnostics")
            if isinstance(diagnostics, list):
                for diagnostic in diagnostics:
                    if not isinstance(diagnostic, dict):
                        continue
                    message = str(diagnostic.get("message") or "").strip()
                    if message:
                        key = (
                            category,
                            "diagnostic",
                            str(diagnostic.get("source") or "runtime"),
                            message,
                        )
                        failure_messages[key] += 1
                        failure_jobs[key].add(str(record.get("job_id") or "unknown"))

    process_report = {}
    for process, counts in sorted(processes.items()):
        process_report[process] = {
            "requests": sum(counts.values()),
            "outcomes": dict(sorted(counts.items())),
            "durations": duration_summary(process_durations[process]),
        }
    coverage_report = {}
    for key, values in sorted(coverage.items()):
        coverage_report[key] = {
            "uses": sum(values.values()),
            "distinct_values": len(values),
            "top_values": [
                {"count": count, "value": value}
                for value, count in values.most_common(top)
            ],
        }
    messages = [
        {
            "count": count,
            "category": key[0],
            "code": key[1] or None,
            "locator": key[2] or None,
            "message": key[3],
            "example_jobs": sorted(failure_jobs[key])[:3],
        }
        for key, count in failure_messages.most_common(top)
    ]
    return {
        "period": {
            "first": min(timestamps).isoformat() if timestamps else None,
            "last": max(timestamps).isoformat() if timestamps else None,
        },
        "requests": len(records),
        "outcomes": dict(sorted(outcomes.items())),
        "durations": {
            name: duration_summary(values) for name, values in sorted(durations.items())
        },
        "processes": process_report,
        "coverage": coverage_report,
        "orchestrate": aggregate_orchestrate(records, top),
        "failure_categories": dict(failure_categories.most_common()),
        "failure_messages": messages,
    }


def format_duration(seconds: object) -> str:
    if not isinstance(seconds, (int, float)):
        return "n/a"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.1f}s"


def print_report(report: dict[str, object]) -> None:
    period = report["period"]
    outcomes = report["outcomes"]
    failures = int(outcomes.get("failed", 0))
    successful = int(outcomes.get("successful", 0))
    total = int(report["requests"])
    rate = (successful / total * 100) if total else 0
    print("PyWPS request summary")
    print(f"Period: {period['first'] or 'n/a'} to {period['last'] or 'n/a'}")
    print(f"Requests: {total}  successful={successful}  failed={failures}  success_rate={rate:.1f}%")

    print("\nProcesses")
    for name, values in report["processes"].items():
        duration = values["durations"]
        print(
            f"  {name}: requests={values['requests']} outcomes={values['outcomes']} "
            f"median={format_duration(duration['median_seconds'])} "
            f"p95={format_duration(duration['p95_seconds'])} "
            f"max={format_duration(duration['max_seconds'])}"
        )

    orchestrate = report["orchestrate"]
    if orchestrate is not None:
        print("\nOrchestrate production data")
        print(
            f"  requests={orchestrate['requests']} "
            f"workflow_lineage={orchestrate['jobs_with_workflow_lineage']} "
            f"missing_lineage={orchestrate['jobs_without_workflow_lineage']}"
        )
        if not orchestrate["collections"]:
            print("  No requested collections were retained in XML lineage.")
        for collection, values in orchestrate["collections"].items():
            print(
                f"  {collection}: requests={values['requests']} "
                f"outcomes={values['outcomes']} years={values['year_coverage']}"
            )
            for time_range in values["time_ranges"]:
                print(f"    {time_range['count']:>5}  time={time_range['value']}")
        print("  Failed data")
        if not orchestrate["failures"]:
            print("    No orchestrate failures.")
        for failure in orchestrate["failures"]:
            jobs = ",".join(failure["example_jobs"])
            print(
                f"    {failure['count']:>5} [{failure['category']}] "
                f"{failure['collection']}: {failure['message']} jobs={jobs}"
            )

    print("\nRequested-data coverage")
    if not report["coverage"]:
        print("  No input lineage was present in the inspected XML records.")
    for name, values in report["coverage"].items():
        print(f"  {name}: uses={values['uses']} distinct={values['distinct_values']}")
        for item in values["top_values"]:
            print(f"    {item['count']:>5}  {item['value']}")

    print("\nFailure causes")
    if not report["failure_categories"]:
        print("  No failures.")
    for category, count in report["failure_categories"].items():
        percentage = (count / failures * 100) if failures else 0
        print(f"  {category}: {count} ({percentage:.1f}%)")
    for item in report["failure_messages"]:
        details = " ".join(
            value for value in (item["code"], item["locator"]) if value
        )
        suffix = f" ({details})" if details else ""
        jobs = ",".join(item["example_jobs"])
        print(
            f"    {item['count']:>5} [{item['category']}] {item['message']}{suffix} "
            f"jobs={jobs}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path, help="request JSONL logs, optionally .gz")
    parser.add_argument("--from", dest="start", help="inclusive ISO date or timestamp")
    parser.add_argument("--to", dest="end", help="inclusive ISO date or timestamp")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--process",
        default="orchestrate",
        help="only include this process identifier (default: orchestrate)",
    )
    selection.add_argument(
        "--all-processes",
        action="store_true",
        help="include every process instead of only orchestrate",
    )
    parser.add_argument("--top", type=int, default=10, help="values/messages per section")
    parser.add_argument("--json", action="store_true", help="write machine-readable JSON")
    args = parser.parse_args(argv)
    if args.top < 1:
        parser.error("--top must be positive")
    try:
        args.start = parse_time(args.start) if args.start else None
        args.end = parse_time(args.end, end=True) if args.end else None
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if args.start and args.end and args.start > args.end:
        parser.error("--from must not be after --to")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    records, errors = load_records(args.logs)
    selected = []
    for record in records:
        timestamp = record_time(record)
        if args.start and (timestamp is None or timestamp < args.start):
            continue
        if args.end and (timestamp is None or timestamp > args.end):
            continue
        if not args.all_processes and record.get("process") != args.process:
            continue
        selected.append(record)
    report = aggregate(selected, args.top)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
