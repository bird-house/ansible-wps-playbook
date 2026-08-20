#!/usr/bin/env python3
"""Show a compact snapshot of recent and active PyWPS database jobs."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from uuid import UUID


UTC = timezone.utc
MAX_DATABASE_UTC_OFFSET = timedelta(hours=14)
UUID_EPOCH_100NS = 0x01B21DD213814000
TIMESTAMP_TOLERANCE = timedelta(minutes=5)
WINDOW_RE = re.compile(r"^([1-9][0-9]*)([mhd])$")
ACTIVE_STATES = {"accepted", "running"}


def parse_window(value: str) -> timedelta:
    match = WINDOW_RE.fullmatch(value.strip().lower())
    if not match:
        raise ValueError("window must be a positive number followed by m, h, or d")
    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def uuid1_timestamp(value: object) -> datetime | None:
    try:
        identifier = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None
    if identifier.version != 1:
        return None
    seconds = (identifier.time - UUID_EPOCH_100NS) / 10_000_000
    return datetime.fromtimestamp(seconds, UTC)


def database_wall_clock_offset(record: object) -> timedelta | None:
    value = getattr(record, "time_start", None)
    identifier_time = uuid1_timestamp(getattr(record, "uuid", None))
    if value is None or value.tzinfo is not None or identifier_time is None:
        return None
    difference = value.replace(tzinfo=UTC) - identifier_time
    offset = timedelta(minutes=round(difference.total_seconds() / 60))
    if abs(offset) > MAX_DATABASE_UTC_OFFSET:
        return None
    if abs(difference - offset) > TIMESTAMP_TOLERANCE:
        return None
    return offset


def database_timestamp(record: object, value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    offset = database_wall_clock_offset(record)
    if offset is not None:
        return value.replace(tzinfo=UTC) - offset
    return value.astimezone(UTC)


def status_name(value: object, wps_status: object) -> str:
    if value == getattr(wps_status, "ACCEPTED", object()):
        return "accepted"
    if value in {
        getattr(wps_status, "STARTED", object()),
        getattr(wps_status, "PAUSED", object()),
    }:
        return "running"
    if value == getattr(wps_status, "SUCCEEDED", object()):
        return "successful"
    if value == getattr(wps_status, "FAILED", object()):
        return "failed"
    if value == getattr(wps_status, "DISMISSED", object()):
        return "dismissed"
    return "other"


def active_age_seconds(record: object, now: datetime) -> float | None:
    started_value = getattr(record, "time_start", None)
    if started_value is None:
        return None
    started = database_timestamp(record, started_value)
    age = (now - started).total_seconds()
    return age if age >= 0 else None


def completed_duration_seconds(record: object) -> float | None:
    started_value = getattr(record, "time_start", None)
    ended_value = getattr(record, "time_end", None)
    if started_value is None or ended_value is None:
        return None
    started = database_timestamp(record, started_value)
    ended = database_timestamp(record, ended_value)
    duration = (ended - started).total_seconds()
    return duration if duration >= 0 else None


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent * len(ordered)) - 1)]


def duration_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "median": statistics.median(values) if values else None,
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def summarize(
    records: Iterable[object],
    wps_status: object,
    *,
    now: datetime,
    window: timedelta,
) -> dict:
    since = now - window
    statuses: Counter[str] = Counter()
    durations: list[float] = []
    process_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    process_durations: dict[str, list[float]] = defaultdict(list)
    active_jobs = []

    for record in records:
        started_value = getattr(record, "time_start", None)
        if started_value is None:
            continue
        started = database_timestamp(record, started_value)
        state = status_name(getattr(record, "status", None), wps_status)
        identifier = str(getattr(record, "identifier", None) or "unknown")
        is_recent = since <= started <= now

        if is_recent:
            statuses[state] += 1
            process_statuses[identifier][state] += 1
            if state not in ACTIVE_STATES:
                duration = completed_duration_seconds(record)
                if duration is not None:
                    durations.append(duration)
                    process_durations[identifier].append(duration)

        if state in ACTIVE_STATES:
            age = active_age_seconds(record, now)
            active_jobs.append(
                {
                    "job_id": str(getattr(record, "uuid", None) or "unknown"),
                    "process": identifier,
                    "status": state,
                    "started": started,
                    "age_seconds": age,
                    "in_window": is_recent,
                }
            )
            if not is_recent:
                process_statuses[identifier][state] += 1

    processes = []
    for identifier, counts in process_statuses.items():
        recent_total = sum(counts.values()) - sum(
            1
            for job in active_jobs
            if job["process"] == identifier and not job["in_window"]
        )
        processes.append(
            {
                "identifier": identifier,
                "requests": recent_total,
                "successful": counts["successful"],
                "failed": counts["failed"],
                "active": counts["accepted"] + counts["running"],
                "other": counts["dismissed"] + counts["other"],
                "duration_seconds": duration_summary(process_durations[identifier]),
            }
        )
    processes.sort(key=lambda item: (-item["requests"], item["identifier"]))
    active_jobs.sort(key=lambda item: item["started"])

    total = sum(statuses.values())
    final = statuses["successful"] + statuses["failed"]
    return {
        "generated_at": now,
        "since": since,
        "window_seconds": int(window.total_seconds()),
        "requests": total,
        "successful": statuses["successful"],
        "failed": statuses["failed"],
        "active_in_window": statuses["accepted"] + statuses["running"],
        "other": statuses["dismissed"] + statuses["other"],
        "success_rate": (statuses["successful"] / final * 100) if final else None,
        "duration_seconds": duration_summary(durations),
        "processes": processes,
        "active_jobs": active_jobs,
    }


def format_duration(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 1:
        return f"{value * 1000:.0f}ms"
    if value < 60:
        return f"{value:.1f}s"
    if value < 3600:
        return f"{value / 60:.1f}m"
    return f"{value / 3600:.1f}h"


def print_table(
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    right: set[int],
) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    for row_number, row in enumerate([headers, *rows]):
        print(
            "  ".join(
                value.rjust(widths[index]) if index in right else value.ljust(widths[index])
                for index, value in enumerate(row)
            ).rstrip()
        )
        if row_number == 0:
            print("  ".join("-" * width for width in widths))


def print_report(report: dict, *, service: str, window_label: str, top: int) -> None:
    local_timezone = datetime.now().astimezone().tzinfo
    generated = report["generated_at"].astimezone(local_timezone)
    since = report["since"].astimezone(local_timezone)
    print(f"PyWPS database — {service} — {generated:%Y-%m-%d %H:%M:%S %Z}")
    print(f"Window: last {window_label} (since {since:%Y-%m-%d %H:%M %Z})")
    success_rate = report["success_rate"]
    rate = f"{success_rate:.1f}%" if success_rate is not None else "n/a"
    print(
        f"Requests: {report['requests']}  success={report['successful']}  "
        f"failures={report['failed']}  active={report['active_in_window']}  "
        f"other={report['other']}  success_rate={rate}"
    )
    duration = report["duration_seconds"]
    print(
        f"Duration: median={format_duration(duration['median'])}  "
        f"p95={format_duration(duration['p95'])}  "
        f"max={format_duration(duration['max'])}"
    )

    print("\nProcesses — window requests; non-final includes all ages")
    rows = []
    for process in report["processes"]:
        process_duration = process["duration_seconds"]
        rows.append(
            (
                process["identifier"],
                str(process["requests"]),
                str(process["successful"]),
                str(process["failed"]),
                str(process["active"]),
                str(process["other"]),
                format_duration(process_duration["median"]),
                format_duration(process_duration["p95"]),
                format_duration(process_duration["max"]),
            )
        )
    if rows:
        print_table(
            (
                "Process",
                "Requests",
                "OK",
                "Failed",
                "Non-final",
                "Other",
                "Median",
                "P95",
                "Max",
            ),
            rows,
            {1, 2, 3, 4, 5, 6, 7, 8},
        )
    else:
        print("No requests in this window.")

    active_jobs = report["active_jobs"]
    print(f"\nNon-final database jobs — all ages ({len(active_jobs)})")
    if not active_jobs:
        print("None")
        return
    active_rows = [
        (
            job["job_id"][:8],
            job["process"],
            job["status"],
            format_duration(job["age_seconds"]),
            job["started"].astimezone(local_timezone).strftime("%Y-%m-%d %H:%M"),
        )
        for job in active_jobs[:top]
    ]
    print_table(("Job", "Process", "Status", "Age", "Started"), active_rows, {3})
    if len(active_jobs) > top:
        print(f"... {len(active_jobs) - top} more active jobs; increase --top to see more")


def load_records(config_path: Path, since: datetime):
    os.environ["PYWPS_CFG"] = str(config_path)
    try:
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS
        from sqlalchemy import create_engine, inspect, or_
        from sqlalchemy.orm import sessionmaker
    except ImportError as error:
        raise RuntimeError(
            "ptop must run with the service Conda environment"
        ) from error

    database_url = configuration.get_config_value("logging", "database")
    engine = create_engine(database_url)
    if not inspect(engine).has_table(dblog.ProcessInstance.__tablename__):
        engine.dispose()
        raise RuntimeError(
            f"PyWPS request table does not exist: {dblog.ProcessInstance.__tablename__}"
        )
    active_values = [
        getattr(WPS_STATUS, name)
        for name in ("ACCEPTED", "STARTED", "PAUSED")
        if hasattr(WPS_STATUS, name)
    ]
    oldest = (since - MAX_DATABASE_UTC_OFFSET).replace(tzinfo=None)
    session = sessionmaker(bind=engine)()
    try:
        query = session.query(dblog.ProcessInstance).filter(
            dblog.ProcessInstance.operation == "execute",
            or_(
                dblog.ProcessInstance.time_start >= oldest,
                dblog.ProcessInstance.status.in_(active_values),
            ),
        )
        return query.all(), WPS_STATUS
    finally:
        session.close()
        engine.dispose()


def json_default(value: object):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--service", required=True)
    parser.add_argument(
        "--window",
        default="1h",
        help="recent window: Nm, Nh, or Nd (default: 1h)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="maximum active jobs shown (default: 10)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.config.is_file():
        parser.error(f"configuration file does not exist: {args.config}")
    if args.top < 1:
        parser.error("--top must be positive")
    try:
        args.window_delta = parse_window(args.window)
    except ValueError as error:
        parser.error(str(error))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = datetime.now(UTC)
    try:
        records, wps_status = load_records(args.config, now - args.window_delta)
        report = summarize(records, wps_status, now=now, window=args.window_delta)
    except Exception as error:
        print(f"ptop: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, default=json_default))
    else:
        print_report(report, service=args.service, window_label=args.window, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
