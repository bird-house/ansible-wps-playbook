#!/usr/bin/env python3
"""Maintain and display durable daily statistics from WPS JSONL events."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import re
import statistics
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from wps_tools_events import open_jsonl


UTC = timezone.utc
FIELDS = (
    "date",
    "service",
    "requests",
    "successful",
    "failed",
    "duration_count",
    "duration_total_seconds",
    "duration_median_seconds",
    "duration_p95_seconds",
    "duration_max_seconds",
    "memory_failures",
    "timeout_failures",
    "recovered_jobs",
    "long_running_jobs",
    "operation_errors",
)
MEMORY_RE = re.compile(r"\b(?:oom|oom-kill|memoryerror)\b|out of memory", re.I)
TIMEOUT_RE = re.compile(r"timed?\s*out|time(?: |-)?limit|walltime|deadline", re.I)


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def event_day(record: dict[str, object]) -> str | None:
    timestamp = parse_timestamp(record.get("finished_at") or record.get("recorded_at"))
    return timestamp.date().isoformat() if timestamp else None


def diagnostic_text(record: dict[str, object]) -> str:
    values = []
    for key in ("failures", "diagnostics"):
        items = record.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                values.extend(str(value or "") for value in item.values())
    return " ".join(values)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def load_events(paths: list[Path]) -> tuple[list[dict[str, object]], list[str]]:
    records = []
    errors = []
    seen = set()
    for path in paths:
        try:
            with open_jsonl(path) as stream:
                for line_number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as error:
                        errors.append(f"{path}:{line_number}: {error.msg}")
                        continue
                    if not isinstance(record, dict):
                        continue
                    record_type = record.get("record_type", "request")
                    if record_type == "request" and record.get("job_id"):
                        identity = ("request", record.get("service"), record.get("job_id"))
                    else:
                        identity = (
                            "operation",
                            record.get("recorded_at"),
                            record.get("event"),
                            record.get("job_id"),
                            record.get("message"),
                        )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    records.append(record)
        except OSError as error:
            errors.append(f"{path}: {error}")
    return records, errors


def daily_rows(records: list[dict[str, object]], service: str) -> dict[str, dict[str, str]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        if record.get("service") not in {None, service}:
            continue
        day = event_day(record)
        if day:
            grouped[day].append(record)
    result = {}
    for day, items in grouped.items():
        requests = [item for item in items if item.get("record_type", "request") == "request"]
        operations = [item for item in items if item.get("record_type") == "operation"]
        durations = [
            float(item["duration_seconds"])
            for item in requests
            if isinstance(item.get("duration_seconds"), (int, float))
        ]
        failed = [item for item in requests if item.get("outcome") == "failed"]
        recovered = {
            str(item["job_id"])
            for item in operations
            if item.get("event") == "job-recovered" and item.get("job_id")
        }
        long_running = {
            str(item["job_id"])
            for item in operations
            if item.get("event") == "job-long-running" and item.get("job_id")
        }
        row: dict[str, object] = {
            "date": day,
            "service": service,
            "requests": len(requests),
            "successful": sum(item.get("outcome") == "successful" for item in requests),
            "failed": len(failed),
            "duration_count": len(durations),
            "duration_total_seconds": round(sum(durations), 3),
            "duration_median_seconds": round(statistics.median(durations), 3) if durations else "",
            "duration_p95_seconds": round(percentile(durations, 0.95), 3) if durations else "",
            "duration_max_seconds": round(max(durations), 3) if durations else "",
            "memory_failures": sum(bool(MEMORY_RE.search(diagnostic_text(item))) for item in failed),
            "timeout_failures": sum(bool(TIMEOUT_RE.search(diagnostic_text(item))) for item in failed),
            "recovered_jobs": len(recovered),
            "long_running_jobs": len(long_running),
            "operation_errors": sum(
                item.get("event") == "operation-error" or item.get("level") == "critical"
                for item in operations
            ),
        }
        result[day] = {field: str(row.get(field, "")) for field in FIELDS}
    return result


def read_csv(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as stream:
        return {row["date"]: row for row in csv.DictReader(stream) if row.get("date")}


def write_csv(path: Path, rows: dict[str, dict[str, str]], keep_days: int) -> None:
    if keep_days:
        cutoff = datetime.now(UTC).date() - timedelta(days=keep_days - 1)
        rows = {key: row for key, row in rows.items() if date.fromisoformat(key) >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows[key] for key in sorted(rows))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def number(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except ValueError:
        return 0


def summary(rows: list[dict[str, str]]) -> dict[str, object]:
    totals = {
        key: int(sum(number(row, key) for row in rows))
        for key in (
            "requests", "successful", "failed", "duration_count", "memory_failures",
            "timeout_failures", "recovered_jobs", "long_running_jobs", "operation_errors",
        )
    }
    duration_total = sum(number(row, "duration_total_seconds") for row in rows)
    duration_max = max((number(row, "duration_max_seconds") for row in rows), default=0)
    return {
        "period": {"first": rows[0]["date"] if rows else None, "last": rows[-1]["date"] if rows else None},
        **totals,
        "success_rate": round(totals["successful"] / totals["requests"] * 100, 1) if totals["requests"] else 0,
        "duration_average_seconds": round(duration_total / totals["duration_count"], 3) if totals["duration_count"] else None,
        "duration_max_seconds": duration_max or None,
        "days": rows,
    }


def duration(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    if value >= 3600:
        return f"{value / 3600:.1f}h"
    if value >= 60:
        return f"{value / 60:.1f}m"
    return f"{value:.1f}s"


def calendar_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid date: {value}") from error


def print_report(service: str, report: dict[str, object]) -> None:
    period = report["period"]
    print(f"WPS statistics — {service}")
    print(f"Period: {period['first'] or 'n/a'} to {period['last'] or 'n/a'}")
    print(
        f"\nRequests: {report['requests']}  success={report['successful']}  "
        f"failures={report['failed']}  success_rate={report['success_rate']:.1f}%"
    )
    print(
        f"Duration: average={duration(report['duration_average_seconds'])}  "
        f"max={duration(report['duration_max_seconds'])}"
    )
    print(
        f"Failures: memory={report['memory_failures']}  timeout={report['timeout_failures']}"
    )
    print(
        f"Operations: recovered={report['recovered_jobs']}  "
        f"long-running={report['long_running_jobs']}  errors={report['operation_errors']}"
    )
    print("\nRecent days")
    print("  Date         Requests  Success  Failures  Recovered  Long-running")
    for row in report["days"][-14:]:
        print(
            f"  {row['date']}  {int(number(row, 'requests')):>8}  "
            f"{int(number(row, 'successful')):>7}  {int(number(row, 'failed')):>8}  "
            f"{int(number(row, 'recovered_jobs')):>9}  "
            f"{int(number(row, 'long_running_jobs')):>12}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="*", type=Path)
    parser.add_argument("--service", required=True)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--from", dest="start", type=calendar_date)
    parser.add_argument("--to", dest="end", type=calendar_date)
    parser.add_argument("--keep-days", type=int, default=0)
    args = parser.parse_args()
    if args.keep_days < 0:
        parser.error("--keep-days cannot be negative")
    if args.start and args.end and args.start > args.end:
        parser.error("--from must not be later than --to")
    return args


def update_rows(
    csv_path: Path,
    logs: list[Path],
    service: str,
    keep_days: int,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Update an aggregate without losing concurrent cron or operator writes."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = csv_path.with_name(f".{csv_path.name}.lock")
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = read_csv(csv_path)
        records, errors = load_events(logs)
        rows.update(daily_rows(records, service))
        write_csv(csv_path, rows, keep_days)
    return rows, errors


def main() -> int:
    args = parse_args()
    if args.update:
        rows, errors = update_rows(
            args.csv, args.logs, args.service, args.keep_days
        )
    else:
        rows, errors = read_csv(args.csv), []
    selected = [
        rows[key] for key in sorted(rows)
        if (not args.start or key >= args.start) and (not args.end or key <= args.end)
    ]
    report = summary(selected)
    if not args.quiet:
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print_report(args.service, report)
    for error in errors:
        print(error, file=os.sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
