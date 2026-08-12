#!/usr/bin/env python3
"""Summarize PyWPS database activity for an explicit time range."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from uuid import UUID


UTC = timezone.utc
MAX_DATABASE_UTC_OFFSET = timedelta(hours=14)
UUID_EPOCH_100NS = 0x01B21DD213814000
TIMESTAMP_TOLERANCE = timedelta(minutes=5)
STATUS_NAMES = (
    "accepted",
    "running",
    "successful",
    "failed",
    "dismissed",
    "unmapped",
)


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime
    value: str


@dataclass(frozen=True)
class ErrorSummary:
    message: str
    count: int
    first: datetime
    last: datetime


@dataclass
class ProcessSummary:
    identifier: str
    statuses: Counter[str]
    durations: list[float]


def parse_endpoint(value: str, *, is_end: bool) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("time range endpoints must not be empty")
    date_only = len(text) == 10
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"invalid ISO timestamp: {value}") from error
    if date_only and is_end:
        parsed = datetime.combine(parsed.date(), time.max)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(UTC)


def parse_time_range(value: str) -> TimeRange:
    try:
        start_text, end_text = value.split("/")
    except ValueError as error:
        raise ValueError("time range must contain one '/' between two timestamps") from error
    start = parse_endpoint(start_text, is_end=False)
    end = parse_endpoint(end_text, is_end=True)
    if start > end:
        raise ValueError("time range start must not be after its end")
    return TimeRange(start=start, end=end, value=value)


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
    return "unmapped"


def empty_statuses() -> Counter[str]:
    return Counter({name: 0 for name in STATUS_NAMES})


def duration_seconds(record: object) -> float | None:
    start_value = getattr(record, "time_start", None)
    end_value = getattr(record, "time_end", None)
    if start_value is None or end_value is None:
        return None
    started = database_timestamp(record, start_value)
    ended = database_timestamp(record, end_value)
    duration = (ended - started).total_seconds()
    return duration if duration >= 0 else None


def summarize(records: Iterable[object], wps_status: object, period: TimeRange) -> dict:
    statuses = empty_statuses()
    processes: dict[str, ProcessSummary] = {}
    errors: dict[str, list[datetime]] = defaultdict(list)
    durations: list[float] = []
    daily = Counter()
    local_timezone = datetime.now().astimezone().tzinfo

    for record in records:
        start_value = getattr(record, "time_start", None)
        if start_value is None:
            continue
        started = database_timestamp(record, start_value)
        if started < period.start or started > period.end:
            continue
        state = status_name(getattr(record, "status", None), wps_status)
        statuses[state] += 1
        daily[started.astimezone(local_timezone).date().isoformat()] += 1

        identifier = str(getattr(record, "identifier", None) or "(unknown)")
        process = processes.setdefault(
            identifier,
            ProcessSummary(identifier, empty_statuses(), []),
        )
        process.statuses[state] += 1
        duration = duration_seconds(record)
        if duration is not None:
            durations.append(duration)
            process.durations.append(duration)
        if state == "failed":
            message = str(getattr(record, "message", None) or "(empty)")
            errors[message].append(started)

    total = sum(statuses.values())
    final = statuses["successful"] + statuses["failed"]
    return {
        "range": {
            "input": period.value,
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
        },
        "requests": {
            "total": total,
            **{name: statuses[name] for name in STATUS_NAMES},
            "success_rate_percent": (
                round(statuses["successful"] * 100 / final, 2) if final else None
            ),
        },
        "requests_per_day": numeric_summary(list(daily.values())),
        "duration_seconds": numeric_summary(durations, include_total=True),
        "processes": [
            {
                "identifier": process.identifier,
                "total": sum(process.statuses.values()),
                **{name: process.statuses[name] for name in STATUS_NAMES},
                "duration_seconds": numeric_summary(
                    process.durations, include_total=True
                ),
            }
            for process in sorted(processes.values(), key=lambda item: item.identifier)
        ],
        "errors": [
            asdict(
                ErrorSummary(
                    message=message,
                    count=len(occurrences),
                    first=min(occurrences),
                    last=max(occurrences),
                )
            )
            for message, occurrences in sorted(
                errors.items(),
                key=lambda item: (-len(item[1]), -max(item[1]).timestamp(), item[0]),
            )
        ],
    }


def numeric_summary(values: list[float], *, include_total: bool = False) -> dict:
    result = {
        "count": len(values),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "median": statistics.median(values) if values else None,
    }
    if include_total:
        result["total"] = sum(values)
    return result


def json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def format_number(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def print_report(report: dict) -> None:
    request = report["requests"]
    success_rate = format_number(request["success_rate_percent"])
    if request["success_rate_percent"] is not None:
        success_rate += "%"
    print(f"range\t{report['range']['start']}\t{report['range']['end']}")
    print("requests\t" + "\t".join(
        [f"total={request['total']}"]
        + [f"{name}={request[name]}" for name in STATUS_NAMES]
        + [f"success_rate={success_rate}"]
    ))
    for label in ("requests_per_day", "duration_seconds"):
        values = report[label]
        fields = ("count", "minimum", "median", "maximum", "total")
        print(label + "\t" + "\t".join(
            f"{name}={format_number(values[name])}"
            for name in fields
            if name in values
    ))

    print("\nprocesses")
    print(
        "identifier\ttotal\taccepted\trunning\tsuccessful\tfailed\t"
        "dismissed\tunmapped\tduration_total_seconds"
    )
    for process in report["processes"]:
        print("\t".join(
            [process["identifier"], str(process["total"])]
            + [str(process[name]) for name in STATUS_NAMES]
            + [format_number(process["duration_seconds"]["total"])]
        ))

    print("\nerrors")
    print("count\tfirst\tlast\tmessage")
    for error in report["errors"]:
        message = json.dumps(error["message"], ensure_ascii=False)
        print(
            f"{error['count']}\t{error['first'].isoformat()}\t"
            f"{error['last'].isoformat()}\t{message}"
        )


def load_records(config_path: Path, period: TimeRange, identifier: str | None):
    os.environ["PYWPS_CFG"] = str(config_path)
    try:
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.orm import sessionmaker
    except ImportError as error:
        raise RuntimeError(
            "db-monitor must run with the service Conda environment"
        ) from error

    database_url = configuration.get_config_value("logging", "database")
    engine = create_engine(database_url)
    if not inspect(engine).has_table(dblog.ProcessInstance.__tablename__):
        engine.dispose()
        raise RuntimeError(
            f"PyWPS request table does not exist: {dblog.ProcessInstance.__tablename__}"
        )
    session = sessionmaker(bind=engine)()
    try:
        oldest = (period.start - MAX_DATABASE_UTC_OFFSET).replace(tzinfo=None)
        newest = (period.end + MAX_DATABASE_UTC_OFFSET).replace(tzinfo=None)
        query = session.query(dblog.ProcessInstance).filter(
            dblog.ProcessInstance.operation == "execute",
            dblog.ProcessInstance.time_start >= oldest,
            dblog.ProcessInstance.time_start <= newest,
        )
        if identifier is not None:
            query = query.filter(dblog.ProcessInstance.identifier == identifier)
        records = query.all()
        return records, WPS_STATUS
    finally:
        session.close()
        engine.dispose()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "time_range",
        metavar="START/END",
        help="ISO timestamps or dates; date-only endpoints include whole days",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="PyWPS service configuration",
    )
    parser.add_argument(
        "--identifier",
        help="include only this PyWPS process identifier",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the complete report as JSON",
    )
    args = parser.parse_args(argv)
    try:
        args.period = parse_time_range(args.time_range)
    except ValueError as error:
        parser.error(str(error))
    if not args.config.is_file():
        parser.error(f"configuration file does not exist: {args.config}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        records, wps_status = load_records(args.config, args.period, args.identifier)
        report = summarize(records, wps_status, args.period)
    except Exception as error:
        print(f"db-monitor: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, default=json_default, ensure_ascii=False))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
