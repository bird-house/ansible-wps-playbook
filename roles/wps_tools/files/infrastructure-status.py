#!/usr/bin/env python3
"""Show a compact snapshot of host infrastructure health."""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO


UTC = timezone.utc
WINDOW_RE = re.compile(r"^([1-9][0-9]*)([mhd])$")


@dataclass(frozen=True)
class Metric:
    timestamp: datetime
    values: dict[str, float]


def parse_window(value: str) -> timedelta:
    match = WINDOW_RE.fullmatch(value.strip().lower())
    if not match:
        raise ValueError("window must be a positive number followed by m, h, or d")
    amount = int(match.group(1))
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[match.group(2)]


def open_metric(path: Path) -> TextIO:
    if path.is_file():
        return path.open(encoding="utf-8", newline="")
    compressed = Path(f"{path}.gz")
    if compressed.is_file():
        return gzip.open(compressed, mode="rt", encoding="utf-8", newline="")
    raise FileNotFoundError(path)


def latest_metric(path: Path, columns: tuple[str, ...]) -> Metric:
    with open_metric(path) as source:
        reader = csv.DictReader(source)
        required = {"epoch", *columns}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = ", ".join(sorted(required - set(reader.fieldnames or ())))
            raise ValueError(f"missing columns in {path}: {missing}")
        latest = None
        for row in reader:
            if not row.get("epoch"):
                continue
            latest = Metric(
                datetime.fromtimestamp(float(row["epoch"]), UTC),
                {name: float(row[name]) for name in columns},
            )
    if latest is None:
        raise ValueError(f"no metric rows in {path}")
    return latest


def metrics_in_file(
    path: Path,
    columns: tuple[str, ...],
    start: datetime,
    end: datetime,
) -> list[Metric]:
    with open_metric(path) as source:
        reader = csv.DictReader(source)
        required = {"epoch", *columns}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"invalid metric columns in {path}")
        result = []
        for row in reader:
            if not row.get("epoch"):
                continue
            timestamp = datetime.fromtimestamp(float(row["epoch"]), UTC)
            if start <= timestamp <= end:
                result.append(
                    Metric(
                        timestamp,
                        {name: float(row[name]) for name in columns},
                    )
                )
    return result


def window_metrics(
    csv_dir: Path,
    plugin: str,
    metric: str,
    columns: tuple[str, ...],
    start: datetime,
    end: datetime,
) -> list[Metric]:
    first_date = start.astimezone().date()
    last_date = end.astimezone().date()
    result = []
    file_date = first_date
    while file_date <= last_date:
        path = csv_dir / plugin / f"{metric}-{file_date.isoformat()}"
        try:
            result.extend(metrics_in_file(path, columns, start, end))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return []
        file_date += timedelta(days=1)
    return sorted(result, key=lambda item: item.timestamp)


def value_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "current": None,
            "change": None,
            "min": None,
            "average": None,
            "max": None,
        }
    return {
        "count": len(values),
        "current": values[-1],
        "change": values[-1] - values[0],
        "min": min(values),
        "average": sum(values) / len(values),
        "max": max(values),
    }


def recent_metric(
    csv_dir: Path,
    plugin: str,
    metric: str,
    columns: tuple[str, ...],
    today: date,
) -> Metric | None:
    for file_date in (today, today - timedelta(days=1)):
        path = csv_dir / plugin / f"{metric}-{file_date.isoformat()}"
        try:
            return latest_metric(path, columns)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
    return None


def read_loadavg(proc_root: Path) -> tuple[float, float, float]:
    values = (proc_root / "loadavg").read_text(encoding="utf-8").split()
    return tuple(float(value) for value in values[:3])


def read_meminfo(proc_root: Path) -> dict[str, int]:
    result = {}
    for line in (proc_root / "meminfo").read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition(":")
        if not separator:
            continue
        fields = value.split()
        if fields:
            result[name] = int(fields[0]) * 1024
    return result


def format_bytes(value: int | float) -> str:
    amount = float(value)
    for suffix in ("B", "K", "M", "G", "T", "P"):
        if abs(amount) < 1024 or suffix == "P":
            return f"{amount:.1f}{suffix}" if suffix != "B" else f"{amount:.0f}{suffix}"
        amount /= 1024
    raise AssertionError("unreachable")


def format_age(value: float) -> str:
    if value < 60:
        return f"{value:.0f}s"
    if value < 3600:
        return f"{value / 60:.1f}m"
    return f"{value / 3600:.1f}h"


def format_window(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def filesystem_rows(
    paths: list[Path],
    history: dict[str, dict[str, float | int | None]] | None = None,
    usage: Callable[[Path], object] = shutil.disk_usage,
) -> list[dict[str, object]]:
    rows = []
    devices = set()
    for path in paths:
        try:
            device = path.stat().st_dev
            disk = usage(path)
        except OSError:
            rows.append({"path": str(path), "error": "unavailable"})
            continue
        if device in devices:
            continue
        devices.add(device)
        percent = disk.used / disk.total * 100 if disk.total else 0.0
        rows.append(
            {
                "path": str(path),
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": percent,
                "history": (history or {}).get(str(path)),
            }
        )
    return rows


def snapshot(
    *,
    csv_dir: Path,
    proc_root: Path,
    filesystems: list[Path],
    collectd_interval: float,
    window: timedelta,
    collectd_disk_mount: Path | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    current = now or datetime.now(UTC)
    since = current - window
    load_metric = recent_metric(
        csv_dir,
        "load",
        "load",
        ("shortterm", "midterm", "longterm"),
        current.astimezone().date(),
    )
    memory_metric = recent_metric(
        csv_dir,
        "memory",
        "memory-used",
        ("value",),
        current.astimezone().date(),
    )
    observed = [
        metric.timestamp for metric in (load_metric, memory_metric) if metric is not None
    ]
    latest = min(observed) if observed else None
    age = max(0.0, (current - latest).total_seconds()) if latest else None

    try:
        fallback_load = read_loadavg(proc_root)
    except (OSError, ValueError):
        fallback_load = (None, None, None)
    load = (
        tuple(load_metric.values[name] for name in ("shortterm", "midterm", "longterm"))
        if load_metric
        else fallback_load
    )
    load_samples = window_metrics(
        csv_dir,
        "load",
        "load",
        ("shortterm",),
        since,
        current,
    )
    load_history = value_summary(
        [metric.values["shortterm"] for metric in load_samples]
    )
    try:
        memory = read_meminfo(proc_root)
    except (OSError, ValueError):
        memory = {}
    total = memory.get("MemTotal")
    available = memory.get("MemAvailable")
    used = memory_metric.values["value"] if memory_metric else None
    if used is None and total is not None and available is not None:
        used = total - available
    memory_percent = used / total * 100 if used is not None and total else None
    memory_samples = window_metrics(
        csv_dir,
        "memory",
        "memory-used",
        ("value",),
        since,
        current,
    )
    memory_history = value_summary(
        [metric.values["value"] for metric in memory_samples]
    )
    swap_total = memory.get("SwapTotal")
    swap_free = memory.get("SwapFree")
    swap_used = (
        max(0, swap_total - swap_free)
        if swap_total is not None and swap_free is not None
        else None
    )

    disk_history = {}
    if collectd_disk_mount is not None:
        disk_samples = window_metrics(
            csv_dir,
            f"df-{disk_plugin_instance(collectd_disk_mount)}",
            "percent_bytes-used",
            ("value",),
            since,
            current,
        )
        disk_history[str(collectd_disk_mount)] = value_summary(
            [metric.values["value"] for metric in disk_samples]
        )

    return {
        "generated_at": current,
        "since": since,
        "window": format_window(window),
        "hostname": socket.gethostname(),
        "collectd": {
            "latest": latest,
            "age_seconds": age,
            "fresh": age is not None and age <= collectd_interval * 2.5,
        },
        "cpu_count": os.cpu_count(),
        "load": load,
        "load_history": load_history,
        "memory": {
            "total": total,
            "used": used,
            "available": available,
            "percent": memory_percent,
        },
        "memory_history": memory_history,
        "swap": {
            "total": swap_total,
            "used": swap_used,
            "percent": (
                swap_used / swap_total * 100
                if swap_used is not None and swap_total
                else None
            ),
        },
        "filesystems": filesystem_rows(filesystems, disk_history),
    }


def disk_plugin_instance(mount_point: Path) -> str:
    if mount_point == Path("/"):
        return "root"
    return str(mount_point).strip("/").replace("/", "-")


def value_or_dash(value: float | None, suffix: str = "") -> str:
    return "-" if value is None else f"{value:.1f}{suffix}"


def signed_or_dash(value: float | None, suffix: str = "") -> str:
    return "-" if value is None else f"{value:+.1f}{suffix}"


def signed_bytes_or_dash(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value >= 0 else "-"
    return f"{sign}{format_bytes(abs(value))}"


def bytes_or_dash(value: float | None) -> str:
    return "-" if value is None else format_bytes(value)


def print_report(report: dict[str, object]) -> None:
    local_timezone = datetime.now().astimezone().tzinfo
    generated = report["generated_at"].astimezone(local_timezone)
    print(f"Infrastructure — {report['hostname']} — {generated:%Y-%m-%d %H:%M:%S %Z}")
    since = report["since"].astimezone(local_timezone)
    print(f"Window: last {report['window']} (since {since:%Y-%m-%d %H:%M %Z})")
    collectd = report["collectd"]
    if collectd["latest"] is None:
        print("Collectd: no recent load or memory data")
    else:
        state = "fresh" if collectd["fresh"] else "stale"
        print(f"Collectd: {state}  age={format_age(collectd['age_seconds'])}")

    load = report["load"]
    load_history = report["load_history"]
    print(
        f"Load: 1m={value_or_dash(load[0])}  5m={value_or_dash(load[1])}  "
        f"15m={value_or_dash(load[2])}  cores={report['cpu_count'] or '-'}"
    )
    print(
        f"Load window (1m): change={signed_or_dash(load_history['change'])}  "
        f"min={value_or_dash(load_history['min'])}  "
        f"avg={value_or_dash(load_history['average'])}  "
        f"max={value_or_dash(load_history['max'])}"
    )
    memory = report["memory"]
    if memory["total"] is None or memory["used"] is None:
        print("Memory: unavailable")
    else:
        available = (
            format_bytes(memory["available"])
            if memory["available"] is not None
            else "-"
        )
        print(
            f"Memory: used={format_bytes(memory['used'])}/{format_bytes(memory['total'])} "
            f"({value_or_dash(memory['percent'], '%')})  available={available}"
        )
    memory_history = report["memory_history"]
    print(
        f"Memory window: change={signed_bytes_or_dash(memory_history['change'])}  "
        f"min={bytes_or_dash(memory_history['min'])}  "
        f"avg={bytes_or_dash(memory_history['average'])}  "
        f"max={bytes_or_dash(memory_history['max'])}"
    )
    swap = report["swap"]
    if swap["total"] is None:
        print("Swap: unavailable")
    elif swap["total"]:
        print(
            f"Swap: used={format_bytes(swap['used'])}/{format_bytes(swap['total'])} "
            f"({value_or_dash(swap['percent'], '%')})"
        )
    else:
        print("Swap: disabled")

    print("\nFilesystems")
    print("Path                         Used     Free    Total    Use  Change    Min    Avg    Max")
    print("-------------------------  -------  -------  -------  -----  ------  -----  -----  -----")
    for row in report["filesystems"]:
        if "error" in row:
            print(f"{row['path']:<25}  unavailable")
            continue
        print(
            f"{row['path']:<25}  {format_bytes(row['used']):>7}  "
            f"{format_bytes(row['free']):>7}  {format_bytes(row['total']):>7}  "
            f"{row['percent']:>4.1f}%  "
            f"{signed_or_dash((row['history'] or {}).get('change'), 'pp'):>6}  "
            f"{value_or_dash((row['history'] or {}).get('min'), '%'):>5}  "
            f"{value_or_dash((row['history'] or {}).get('average'), '%'):>5}  "
            f"{value_or_dash((row['history'] or {}).get('max'), '%'):>5}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", required=True, type=Path)
    parser.add_argument("--collectd-interval", type=float, default=60)
    parser.add_argument("--window", default="1h")
    parser.add_argument("--collectd-disk-mount", type=Path)
    parser.add_argument("--filesystem", action="append", type=Path, default=[])
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    args = parser.parse_args(argv)
    if args.collectd_interval <= 0:
        parser.error("--collectd-interval must be positive")
    try:
        args.window_delta = parse_window(args.window)
    except ValueError as error:
        parser.error(str(error))
    if not args.filesystem:
        args.filesystem = [Path("/")]
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = snapshot(
            csv_dir=args.csv_dir,
            proc_root=args.proc_root,
            filesystems=args.filesystem,
            collectd_interval=args.collectd_interval,
            window=args.window_delta,
            collectd_disk_mount=args.collectd_disk_mount,
        )
        print_report(report)
    except Exception as error:
        print(f"itop: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
