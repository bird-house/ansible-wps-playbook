#!/usr/bin/env python3
"""Show a compact snapshot of host infrastructure health."""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO


UTC = timezone.utc


@dataclass(frozen=True)
class Metric:
    timestamp: datetime
    values: dict[str, float]


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


def filesystem_rows(
    paths: list[Path],
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
            }
        )
    return rows


def snapshot(
    *,
    csv_dir: Path,
    proc_root: Path,
    filesystems: list[Path],
    collectd_interval: float,
    now: datetime | None = None,
) -> dict[str, object]:
    current = now or datetime.now(UTC)
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

    return {
        "generated_at": current,
        "hostname": socket.gethostname(),
        "collectd": {
            "latest": latest,
            "age_seconds": age,
            "fresh": age is not None and age <= collectd_interval * 2.5,
        },
        "cpu_count": os.cpu_count(),
        "load": load,
        "memory": {
            "total": total,
            "used": used,
            "available": available,
            "percent": memory_percent,
        },
        "filesystems": filesystem_rows(filesystems),
    }


def value_or_dash(value: float | None, suffix: str = "") -> str:
    return "-" if value is None else f"{value:.1f}{suffix}"


def print_report(report: dict[str, object]) -> None:
    local_timezone = datetime.now().astimezone().tzinfo
    generated = report["generated_at"].astimezone(local_timezone)
    print(f"Infrastructure — {report['hostname']} — {generated:%Y-%m-%d %H:%M:%S %Z}")
    collectd = report["collectd"]
    if collectd["latest"] is None:
        print("Collectd: no recent load or memory data")
    else:
        state = "fresh" if collectd["fresh"] else "stale"
        print(f"Collectd: {state}  age={format_age(collectd['age_seconds'])}")

    load = report["load"]
    print(
        f"Load: 1m={value_or_dash(load[0])}  5m={value_or_dash(load[1])}  "
        f"15m={value_or_dash(load[2])}  cores={report['cpu_count'] or '-'}"
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

    print("\nFilesystems")
    print("Path                         Used     Free    Total    Use")
    print("-------------------------  -------  -------  -------  -----")
    for row in report["filesystems"]:
        if "error" in row:
            print(f"{row['path']:<25}  unavailable")
            continue
        print(
            f"{row['path']:<25}  {format_bytes(row['used']):>7}  "
            f"{format_bytes(row['free']):>7}  {format_bytes(row['total']):>7}  "
            f"{row['percent']:>4.1f}%"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", required=True, type=Path)
    parser.add_argument("--collectd-interval", type=float, default=60)
    parser.add_argument("--filesystem", action="append", type=Path, default=[])
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    args = parser.parse_args(argv)
    if args.collectd_interval <= 0:
        parser.error("--collectd-interval must be positive")
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
        )
        print_report(report)
    except Exception as error:
        print(f"itop: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
