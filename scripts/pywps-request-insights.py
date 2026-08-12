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
    return " ".join(parts)


def failure_category(record: dict[str, object]) -> str:
    text = failure_text(record)
    for name, pattern in FAILURE_PATTERNS:
        if pattern.search(text):
            return name
    return "other" if text.strip() else "unknown"


def aggregate(records: list[dict[str, object]], top: int) -> dict[str, object]:
    outcomes: Counter[str] = Counter()
    processes: dict[str, Counter[str]] = defaultdict(Counter)
    process_durations: dict[str, list[float]] = defaultdict(list)
    durations: dict[str, list[float]] = defaultdict(list)
    coverage: dict[str, Counter[str]] = defaultdict(Counter)
    failure_categories: Counter[str] = Counter()
    failure_messages: Counter[tuple[str, str, str, str]] = Counter()
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
                key = f"{process}.{name}"
                for value in values:
                    if isinstance(value, dict):
                        rendered = json.dumps(
                            {item: value.get(item) for item in ("type", "value")},
                            sort_keys=True,
                            ensure_ascii=False,
                        )
                    else:
                        rendered = json.dumps(value, ensure_ascii=False)
                    coverage[key][rendered] += 1

        if outcome == "failed":
            category = failure_category(record)
            failure_categories[category] += 1
            failures = record.get("failures")
            if isinstance(failures, list):
                for failure in failures:
                    if not isinstance(failure, dict):
                        continue
                    failure_messages[
                        (
                            category,
                            str(failure.get("code") or ""),
                            str(failure.get("locator") or ""),
                            str(failure.get("message") or "unknown failure"),
                        )
                    ] += 1

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
        print(f"    {item['count']:>5} [{item['category']}] {item['message']}{suffix}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path, help="request JSONL logs, optionally .gz")
    parser.add_argument("--from", dest="start", help="inclusive ISO date or timestamp")
    parser.add_argument("--to", dest="end", help="inclusive ISO date or timestamp")
    parser.add_argument("--process", help="only include this process identifier")
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
        if args.process and record.get("process") != args.process:
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
