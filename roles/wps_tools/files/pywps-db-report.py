#!/usr/bin/env python3
"""Report PyWPS database activity for an explicit time range."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
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
MAX_DISPLAY_ERROR_LENGTH = 300
TRUNCATION_MARKER = " [..]"
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
    start: datetime | None
    end: datetime | None
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


YEAR_RE = re.compile(r"^[0-9]{4}$")
MONTH_RE = re.compile(r"^[0-9]{4}-[0-9]{2}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def parse_endpoint(value: str, *, is_end: bool) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("time range endpoints must not be empty")
    try:
        if YEAR_RE.fullmatch(text):
            year = int(text)
            parsed = datetime.combine(
                date(year, 12, 31) if is_end else date(year, 1, 1),
                time.max if is_end else time.min,
            )
        elif MONTH_RE.fullmatch(text):
            year, month = (int(part) for part in text.split("-"))
            day = calendar.monthrange(year, month)[1] if is_end else 1
            parsed = datetime.combine(
                date(year, month, day),
                time.max if is_end else time.min,
            )
        else:
            normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
            parsed = datetime.fromisoformat(normalized)
            if DATE_RE.fullmatch(text) and is_end:
                parsed = datetime.combine(parsed.date(), time.max)
    except ValueError as error:
        raise ValueError(f"invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(UTC)


def parse_time_range(value: str) -> TimeRange:
    text = value.strip()
    if "/" not in text:
        if not any(pattern.fullmatch(text) for pattern in (YEAR_RE, MONTH_RE, DATE_RE)):
            raise ValueError(
                "a single range value must be a year, month, or date"
            )
        start_text = end_text = text
    else:
        parts = text.split("/")
        if len(parts) != 2:
            raise ValueError("time range must contain at most one '/'")
        start_text, end_text = parts
    start = parse_endpoint(start_text, is_end=False) if start_text.strip() else None
    end = parse_endpoint(end_text, is_end=True) if end_text.strip() else None
    if start is not None and end is not None and start > end:
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
    successful_durations: list[float] = []
    daily = Counter()
    local_timezone = datetime.now().astimezone().tzinfo

    for record in records:
        start_value = getattr(record, "time_start", None)
        if start_value is None:
            continue
        started = database_timestamp(record, start_value)
        if period.start is not None and started < period.start:
            continue
        if period.end is not None and started > period.end:
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
            if state == "successful":
                successful_durations.append(duration)
        if state == "failed":
            message = str(getattr(record, "message", None) or "(empty)")
            errors[message].append(started)

    total = sum(statuses.values())
    final = statuses["successful"] + statuses["failed"]
    return {
        "range": {
            "input": period.value,
            "start": period.start.isoformat() if period.start is not None else None,
            "end": period.end.isoformat() if period.end is not None else None,
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
        "successful_duration_seconds": duration_distribution(
            successful_durations
        ),
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


def duration_distribution(values: list[float]) -> dict:
    return {
        "count": len(values),
        "under_1_minute": sum(value < 60 for value in values),
        "from_1_to_under_10_minutes": sum(60 <= value < 600 for value in values),
        "from_10_to_under_30_minutes": sum(
            600 <= value < 1800 for value in values
        ),
        "30_minutes_or_more": sum(value >= 1800 for value in values),
        "maximum": max(values) if values else None,
    }


def json_default(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def format_number(value: object, *, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def format_duration(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 1:
        return f"{value * 1000:.0f}ms"
    if value < 60:
        return f"{value:.1f}s"
    seconds = int(round(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def format_local_timestamp(value: str | datetime) -> str:
    timestamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def print_table(
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    *,
    right_aligned: set[int] | None = None,
) -> None:
    right_aligned = right_aligned or set()
    widths = [
        max([len(headers[index])] + [len(row[index]) for row in rows])
        for index in range(len(headers))
    ]

    def formatted(row: tuple[str, ...]) -> str:
        cells = []
        for index, value in enumerate(row):
            align = ">" if index in right_aligned else "<"
            cells.append(f"{value:{align}{widths[index]}}")
        return "  ".join(cells).rstrip()

    print(formatted(headers))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(formatted(row))


def display_error_message(value: object) -> str:
    message = str(value)
    if len(message) <= MAX_DISPLAY_ERROR_LENGTH:
        return message
    retained = MAX_DISPLAY_ERROR_LENGTH - len(TRUNCATION_MARKER)
    return f"{message[:retained].rstrip()}{TRUNCATION_MARKER}"


def print_report(report: dict, *, failures: bool = False, top: int = 10) -> None:
    request = report["requests"]
    total = request["total"]
    final = request["successful"] + request["failed"]
    print("PyWPS database report")
    range_start = (
        format_local_timestamp(report["range"]["start"])
        if report["range"]["start"] is not None
        else "unbounded"
    )
    range_end = (
        format_local_timestamp(report["range"]["end"])
        if report["range"]["end"] is not None
        else "unbounded"
    )
    print(
        f"Range: {range_start}  ->  {range_end}"
    )
    print()

    status_rows = [("Total", str(total), "100.00%" if total else "n/a")]
    for name in STATUS_NAMES:
        count = request[name]
        if count:
            share = f"{count * 100 / total:.2f}%" if total else "n/a"
            status_rows.append((name.replace("_", " ").title(), str(count), share))
    print("Requests")
    print_table(("Status", "Count", "Share"), status_rows, right_aligned={1, 2})
    success_rate = request["success_rate_percent"]
    rate_text = f"{success_rate:.2f}%" if success_rate is not None else "n/a"
    print(f"Success rate: {rate_text} of {final} final requests")

    daily = report["requests_per_day"]
    durations = report["duration_seconds"]
    print("\nTiming")
    print(
        "Requests/day: "
        f"min {format_number(daily['minimum'])}  "
        f"median {format_number(daily['median'])}  "
        f"max {format_number(daily['maximum'])}"
    )
    print(
        f"Recorded durations ({durations['count']}): "
        f"min {format_duration(durations['minimum'])}  "
        f"median {format_duration(durations['median'])}  "
        f"max {format_duration(durations['maximum'])}  "
        f"total {format_duration(durations['total'])}"
    )

    successful_durations = report["successful_duration_seconds"]
    measured = successful_durations["count"]
    duration_rows = []
    for label, key in (
        ("< 1 minute", "under_1_minute"),
        ("1 to < 10 minutes", "from_1_to_under_10_minutes"),
        ("10 to < 30 minutes", "from_10_to_under_30_minutes"),
        (">= 30 minutes", "30_minutes_or_more"),
    ):
        count = successful_durations[key]
        share = f"{count * 100 / measured:.1f}%" if measured else "n/a"
        duration_rows.append((label, str(count), share))
    print(f"\nSuccessful job durations ({measured} measured)")
    print_table(("Runtime", "Jobs", "Share"), duration_rows, right_aligned={1, 2})
    print(
        "Longest successful job: "
        f"{format_duration(successful_durations['maximum'])}"
    )

    print("\nProcesses")
    process_rows = []
    for process in sorted(
        report["processes"],
        key=lambda item: (-item["total"], item["identifier"]),
    ):
        process_final = process["successful"] + process["failed"]
        process_rate = (
            f"{process['successful'] * 100 / process_final:.1f}%"
            if process_final
            else "n/a"
        )
        active = process["accepted"] + process["running"]
        other = process["dismissed"] + process["unmapped"]
        process_rows.append(
            (
                process["identifier"],
                str(process["total"]),
                str(process["successful"]),
                str(process["failed"]),
                str(active),
                str(other),
                process_rate,
                format_duration(process["duration_seconds"]["total"]),
            )
        )
    print_table(
        (
            "Process",
            "Total",
            "OK",
            "Failed",
            "Active",
            "Other",
            "Success",
            "Duration",
        ),
        process_rows,
        right_aligned={1, 2, 3, 4, 5, 6, 7},
    )

    if not failures:
        return
    errors = report["errors"]
    error_count = sum(error["count"] for error in errors)
    shown = min(top, len(errors))
    heading = f"\nFailure details ({error_count} failures, {len(errors)} unique messages)"
    if shown < len(errors):
        heading += f"; showing {shown}, increase --top to see more"
    print(heading)
    if not errors:
        print("None")
    for error in errors[:top]:
        message = json.dumps(
            display_error_message(error["message"]), ensure_ascii=False
        )
        print(f"\n{error['count']}x  {message}")
        print(
            f"    First: {format_local_timestamp(error['first'])}\n"
            f"    Last:  {format_local_timestamp(error['last'])}"
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
            "db-report must run with the service Conda environment"
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
        query = session.query(dblog.ProcessInstance).filter(
            dblog.ProcessInstance.operation == "execute",
        )
        if period.start is not None:
            oldest = (period.start - MAX_DATABASE_UTC_OFFSET).replace(tzinfo=None)
            query = query.filter(dblog.ProcessInstance.time_start >= oldest)
        if period.end is not None:
            newest = (period.end + MAX_DATABASE_UTC_OFFSET).replace(tzinfo=None)
            query = query.filter(dblog.ProcessInstance.time_start <= newest)
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
        nargs="?",
        metavar="[START][/END]",
        help=(
            "optional year, month, date, or ISO timestamp bounds; "
            "omit both bounds to report all time"
        ),
    )
    parser.add_argument(
        "--from",
        dest="from_value",
        metavar="START",
        help="optional inclusive lower year, month, date, or timestamp bound",
    )
    parser.add_argument(
        "--to",
        dest="to_value",
        metavar="END",
        help="optional inclusive upper year, month, date, or timestamp bound",
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
        "--failures",
        action="store_true",
        help="include grouped failure messages in the text report",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="maximum unique failure messages in the text report (default: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the complete report as JSON",
    )
    args = parser.parse_args(argv)
    if args.top < 1:
        parser.error("--top must be positive")
    if args.time_range is not None and (
        args.from_value is not None or args.to_value is not None
    ):
        parser.error("positional range cannot be combined with --from or --to")
    range_value = args.time_range
    if range_value is None:
        range_value = f"{args.from_value or ''}/{args.to_value or ''}"
    try:
        args.period = parse_time_range(range_value)
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
        print(f"db-report: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, default=json_default, ensure_ascii=False))
    else:
        print_report(report, failures=args.failures, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
