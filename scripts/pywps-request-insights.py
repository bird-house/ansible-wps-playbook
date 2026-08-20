#!/usr/bin/env python3
"""Turn PyWPS request logs into coverage, performance, and failure insights."""

from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import sys
import textwrap
from collections import Counter, defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Iterable, TextIO


UTC = timezone.utc
DETAIL_CATEGORY_PRIORITY = ("memory", "timeout")
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
        "no-data",
        re.compile(
            r"no valid data points|no data (?:found|available)|empty (?:subset|selection)|"
            r"no timesteps? (?:are )?matching (?:the )?selection criteria",
            re.IGNORECASE,
        ),
    ),
    (
        "spatial",
        re.compile(
            r"not within (?:the )?(?:longitude|latitude) bounds|longitude frame|"
            r"outside (?:the )?(?:spatial|longitude|latitude) bounds|invalid bounding box",
            re.IGNORECASE,
        ),
    ),
    (
        "input",
        re.compile(
            r"invalid (?:input|parameter)|missing (?:input|parameter)|not found|"
            r"unavailable|permission denied|access denied|"
            r"cannot create TimeComponentsParameter",
            re.IGNORECASE,
        ),
    ),
    (
        "scheduler",
        re.compile(r"\b(?:slurm|drmaa|scheduler|sbatch|srun|squeue)\b", re.IGNORECASE),
    ),
)
GENERIC_FAILURE_RE = re.compile(
    r"^(?:NoApplicableCode\s+(?:None\s+)?)?"
    r"(?:Process failed, please check server error log|Process error: unknown)\s*$",
    re.IGNORECASE,
)
STEP_OUTPUT_RE = re.compile(r"^[A-Za-z0-9_.-]+/output$")
NO_DATA_RE = re.compile(
    r"There were no valid data points found in the requested subset\."
    r"(?: Please expand the area covered by the bounding box, the time period "
    r"or the level range you have selected\.)?",
    re.IGNORECASE,
)
SLURM_TIME_LIMIT_RE = re.compile(
    r"slurmstepd: error: \*{3} JOB .+? DUE TO TIME LIMIT \*{3}",
    re.IGNORECASE,
)
SLURM_OOM_MESSAGE_RE = re.compile(
    r"slurmstepd: error: Detected [1-9][0-9]* oom-kill event\(s\)[^.]*\.?",
    re.IGNORECASE,
)
MISSING_DATETIME_RE = re.compile(
    r"cftime\.[A-Za-z0-9_]+\s*\(?\s*([12][0-9]{3})\s*,",
    re.IGNORECASE,
)
TIME_COMPONENTS_PREFIX_RE = re.compile(
    r"Cannot create TimeComponentsParameter from:\s*",
    re.IGNORECASE,
)
TRACEBACK_BOUNDARY_RE = re.compile(
    r"\s+(?:During handling of the above exception|Traceback \(most recent call last\))",
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


def detail_category_order(counts: dict[str, int] | Counter[str]) -> list[str]:
    prioritized = [name for name in DETAIL_CATEGORY_PRIORITY if counts.get(name, 0)]
    remaining = sorted(
        (name for name in counts if name not in DETAIL_CATEGORY_PRIORITY),
        key=lambda name: (-counts[name], name),
    )
    return prioritized + remaining


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
        return component_years, ranges
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
                if not STEP_OUTPUT_RE.fullmatch(collection):
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
                resolved = aliases.get(alias, [reference] if reference not in aliases else [])
                selected.extend(
                    collection
                    for collection in resolved
                    if not STEP_OUTPUT_RE.fullmatch(collection)
                )
            if not selected:
                selected = [
                    item
                    for values in aliases.values()
                    for item in values
                    if not STEP_OUTPUT_RE.fullmatch(item)
                ]
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
                return concise_failure_message(str(diagnostic["message"]))
    failures = record.get("failures")
    if isinstance(failures, list):
        for failure in failures:
            if isinstance(failure, dict) and failure.get("message"):
                return concise_failure_message(str(failure["message"]))
    return "unknown failure"


def concise_failure_message(message: str) -> str:
    normalized = " ".join(message.split())
    time_limit = SLURM_TIME_LIMIT_RE.search(normalized)
    if time_limit:
        return time_limit.group(0)
    oom = SLURM_OOM_MESSAGE_RE.search(normalized)
    if oom:
        return oom.group(0)
    no_data = NO_DATA_RE.search(normalized)
    if no_data:
        return no_data.group(0)
    if "Requested datetimes include some not found in the dataset" in normalized:
        years = sorted({int(year) for year in MISSING_DATETIME_RE.findall(normalized)})
        if years:
            return "Requested years not found in dataset: " + ",".join(map(str, years))
    time_components = list(TIME_COMPONENTS_PREFIX_RE.finditer(normalized))
    if time_components:
        value = normalized[time_components[-1].end() :]
        value = TRACEBACK_BOUNDARY_RE.split(value, maxsplit=1)[0].strip()
        return f"Invalid time components: {value}"
    exceptions = re.findall(
        r"(?:ProcessError|ValueError|MemoryError|TimeoutError):\s*"
        r"(.+?)(?=\s+During handling|\s+Traceback|$)",
        normalized,
    )
    if exceptions:
        return exceptions[-1].strip()
    return normalized


def aggregate_orchestrate(
    records: list[dict[str, object]], top: int, sort_by: str
) -> dict[str, object] | None:
    orchestrate = [record for record in records if record.get("process") == "orchestrate"]
    if not orchestrate:
        return None
    lineage_jobs = 0
    collection_counts: dict[str, Counter[str]] = defaultdict(Counter)
    collection_years: dict[str, set[int]] = defaultdict(set)
    collection_ranges: dict[str, Counter[str]] = defaultdict(Counter)
    failures: Counter[tuple[str, str, str, str, tuple[str, ...]]] = Counter()
    failure_jobs: dict[
        tuple[str, str, str, str, tuple[str, ...]], set[str]
    ] = defaultdict(set)
    outcomes: Counter[str] = Counter()
    failure_categories: Counter[str] = Counter()
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
            failure_categories[category] += 1
            message = primary_failure_message(record)
            for collection in targets:
                details = requested.get(collection, {"years": set(), "ranges": []})
                years = compact_years(details["years"])
                ranges = tuple(sorted(set(details["ranges"])))
                key = (collection, years, ranges, category, message)
                failures[key] += 1
                failure_jobs[key].add(str(record.get("job_id") or "unknown"))
    collections = {}
    collection_items = list(collection_counts.items())
    if sort_by == "name":
        collection_items.sort(key=lambda item: item[0])
    else:
        metric = "successful" if sort_by == "successful" else "failed" if sort_by == "failed" else None
        collection_items.sort(
            key=lambda item: (
                -(item[1].get(metric, 0) if metric else sum(item[1].values())),
                item[0],
            )
        )
    for collection, counts in collection_items:
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
    failures_by_category = defaultdict(list)
    for item in failures.items():
        failures_by_category[item[0][3]].append(item)
    for items in failures_by_category.values():
        items.sort(key=lambda item: (-item[1], item[0][0], item[0][1:3], item[0][4]))
    category_order = detail_category_order(failure_categories)
    failure_items = []
    group_index = 0
    while len(failure_items) < top:
        added = False
        for category in category_order:
            items = failures_by_category[category]
            if group_index < len(items):
                failure_items.append(items[group_index])
                added = True
                if len(failure_items) == top:
                    break
        if not added:
            break
        group_index += 1
    failure_report = [
        {
            "count": count,
            "collection": key[0],
            "years": key[1],
            "time_ranges": list(key[2]),
            "category": key[3],
            "message": key[4],
            "example_jobs": sorted(failure_jobs[key])[:3],
        }
        for key, count in failure_items[:top]
    ]
    return {
        "requests": len(orchestrate),
        "outcomes": dict(sorted(outcomes.items())),
        "jobs_with_workflow_lineage": lineage_jobs,
        "jobs_without_workflow_lineage": len(orchestrate) - lineage_jobs,
        "collections": collections,
        "failure_categories": dict(failure_categories.most_common()),
        "failure_group_count": len(failures),
        "failures": failure_report,
    }


def aggregate(
    records: list[dict[str, object]], top: int, sort_by: str = "name"
) -> dict[str, object]:
    outcomes: Counter[str] = Counter()
    processes: dict[str, Counter[str]] = defaultdict(Counter)
    process_durations: dict[str, list[float]] = defaultdict(list)
    durations: dict[str, list[float]] = defaultdict(list)
    coverage: dict[str, Counter[str]] = defaultdict(Counter)
    failure_categories: Counter[str] = Counter()
    failure_messages: Counter[tuple[str, str, str, str]] = Counter()
    failure_jobs: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    timestamps = []
    services = set()

    for record in records:
        service = record.get("service")
        if isinstance(service, str) and service:
            services.add(service)
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
        "services": sorted(services),
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
        "orchestrate": aggregate_orchestrate(records, top, sort_by),
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


def format_timestamp(value: object) -> str:
    if not isinstance(value, str):
        return "n/a"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def print_detail_field(label: str, value: str, *, indent: int = 4) -> None:
    prefix = f"{' ' * indent}{label}: "
    print(
        textwrap.fill(
            value,
            width=100,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def print_report(report: dict[str, object], *, details: bool = False) -> None:
    period = report["period"]
    outcomes = report["outcomes"]
    failures = int(outcomes.get("failed", 0))
    successful = int(outcomes.get("successful", 0))
    total = int(report["requests"])
    rate = (successful / total * 100) if total else 0
    services = report.get("services", [])
    service_suffix = f" — {services[0]}" if len(services) == 1 else ""
    print(f"PyWPS request insights{service_suffix}")
    print(
        f"Period: {format_timestamp(period['first'])} "
        f"to {format_timestamp(period['last'])}"
    )
    print(
        f"\nRequests: {total}  success={successful}  failures={failures} "
        f" success_rate={rate:.1f}%"
    )
    duration = report["durations"].get("all", {})
    print(
        f"Duration: median={format_duration(duration.get('median_seconds'))} "
        f" p95={format_duration(duration.get('p95_seconds'))} "
        f" max={format_duration(duration.get('max_seconds'))}"
    )

    if len(report["processes"]) > 1:
        print("\nProcesses")
        for name, values in report["processes"].items():
            duration = values["durations"]
            process_outcomes = values["outcomes"]
            print(
                f"  {name}: requests={values['requests']} "
                f"success={process_outcomes.get('successful', 0)} "
                f"failures={process_outcomes.get('failed', 0)} "
                f"median={format_duration(duration['median_seconds'])} "
                f"p95={format_duration(duration['p95_seconds'])} "
                f"max={format_duration(duration['max_seconds'])}"
            )

    orchestrate = report["orchestrate"]
    if orchestrate is not None:
        print(
            f"Metadata: available={orchestrate['jobs_with_workflow_lineage']} "
            f" missing={orchestrate['jobs_without_workflow_lineage']}"
        )
        print("\nFailure causes")
        if not orchestrate["failure_categories"]:
            print("  No failures.")
        for category, count in orchestrate["failure_categories"].items():
            print(f"  {category}: {count}")
        print(f"\nDatasets ({len(orchestrate['collections'])})")
        if not orchestrate["collections"]:
            print("  No datasets were identified in the retained request metadata.")
        for collection, values in orchestrate["collections"].items():
            outcomes = values["outcomes"]
            print(
                f"  {collection}: requests={values['requests']} "
                f"success={outcomes.get('successful', 0)} "
                f"failures={outcomes.get('failed', 0)} "
                f"years={values['year_coverage']}"
            )
        if details:
            shown = len(orchestrate["failures"])
            groups = orchestrate["failure_group_count"]
            heading = "\nFailure details"
            if shown < groups:
                heading += f" (showing {shown} of {groups} groups; increase --top to see more)"
            print(heading)
            if not orchestrate["failures"]:
                print("  No failures.")
            failures_by_category: dict[str, list[dict[str, object]]] = {}
            for failure in orchestrate["failures"]:
                failures_by_category.setdefault(failure["category"], []).append(failure)
            for category_index, category in enumerate(
                detail_category_order(orchestrate["failure_categories"])
            ):
                category_failures = failures_by_category.get(category, [])
                if not category_failures:
                    continue
                if category_index:
                    print()
                category_total = orchestrate["failure_categories"][category]
                failure_label = "failure" if category_total == 1 else "failures"
                print(f"  {category.capitalize()} ({category_total} {failure_label})")
                failures_by_collection: dict[str, list[dict[str, object]]] = {}
                for failure in category_failures:
                    failures_by_collection.setdefault(failure["collection"], []).append(
                        failure
                    )
                for collection_index, (collection, collection_failures) in enumerate(
                    failures_by_collection.items()
                ):
                    if collection_index:
                        print()
                    print_detail_field("Dataset", collection, indent=4)
                    for failure_index, failure in enumerate(collection_failures):
                        if failure_index:
                            print()
                        count = failure["count"]
                        request_label = "request" if count == 1 else "requests"
                        print(f"      {count} {request_label}")
                        print_detail_field(
                            "Selection",
                            f"years={failure['years']}  "
                            f"time={','.join(failure['time_ranges']) or 'unknown'}",
                            indent=8,
                        )
                        print_detail_field("Reason", failure["message"], indent=8)
                        print_detail_field(
                            "Jobs", ", ".join(failure["example_jobs"]), indent=8
                        )

    if set(report["processes"]) != {"orchestrate"}:
        print("\nRequested-data coverage")
        if not report["coverage"]:
            print("  No input lineage was present in the inspected XML records.")
        for name, values in report["coverage"].items():
            print(f"  {name}: uses={values['uses']} distinct={values['distinct_values']}")
            for item in values["top_values"]:
                print(f"    {item['count']:>5}  {item['value']}")

        heading = "All-process failure causes" if orchestrate is not None else "Failure causes"
        print(f"\n{heading}")
        if not report["failure_categories"]:
            print("  No failures.")
        for category, count in report["failure_categories"].items():
            percentage = (count / failures * 100) if failures else 0
            print(f"  {category}: {count} ({percentage:.1f}%)")
        if details:
            for item in report["failure_messages"]:
                context = " ".join(
                    value for value in (item["code"], item["locator"]) if value
                )
                suffix = f" ({context})" if context else ""
                jobs = ",".join(item["example_jobs"])
                print(
                    f"    {item['count']:>5} [{item['category']}] "
                    f"{item['message']}{suffix} jobs={jobs}"
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
    parser.add_argument(
        "--details",
        action="store_true",
        help="include grouped failure messages and example job IDs",
    )
    parser.add_argument(
        "--sort",
        choices=("name", "requests", "successful", "failed"),
        default="name",
        help="order collections by name or descending frequency (default: name)",
    )
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
    report = aggregate(selected, args.top, args.sort)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report, details=args.details)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
