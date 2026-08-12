#!/usr/bin/env python3
"""Show active Slurm jobs and their batch-step peak memory usage."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Sequence


@dataclass(frozen=True)
class Job:
    job_id: str
    partition: str
    state: str
    elapsed: str
    time_limit: str
    memory_limit: str


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
MEMORY_UNITS = {
    "": 1024 * 1024,  # squeue's %m is expressed in MB when unitless.
    "B": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
    "P": 1024**5,
}


def slurm_environment() -> dict[str, str]:
    return {**os.environ, "LC_ALL": "C"}


def parse_memory(value: str) -> int | None:
    """Convert a Slurm memory value to bytes, or return None for no value."""
    text = value.strip().upper()
    if not text or text in {"N/A", "UNKNOWN", "UNLIMITED"}:
        return None

    # Slurm may mark a request as per-CPU (c) or per-node (n). The local
    # playbook uses per-node limits; ignoring the marker still parses its size.
    if text[-1:] in {"C", "N"}:
        text = text[:-1]
    unit = text[-1:] if text[-1:] in MEMORY_UNITS and not text[-1:].isdigit() else ""
    number = text[:-1] if unit else text
    try:
        amount = Decimal(number)
    except InvalidOperation as error:
        raise ValueError(f"invalid Slurm memory value: {value}") from error
    if amount < 0:
        raise ValueError(f"invalid Slurm memory value: {value}")
    return int(amount * MEMORY_UNITS[unit])


def format_memory(byte_count: int | None) -> str:
    if byte_count is None:
        return "-"
    amount = float(byte_count)
    for unit in ("B", "K", "M", "G", "T"):
        if amount < 1024 or unit == "T":
            if unit in {"B", "K", "M"} or amount >= 100:
                return f"{amount:.0f}{unit}"
            return f"{amount:.1f}{unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def list_jobs(
    user: str | None, runner: CommandRunner = subprocess.run
) -> list[Job]:
    command = [
        "squeue",
        "--noheader",
        "--array",
        "--states",
        "PENDING,RUNNING",
        "--format",
        "%i|%P|%t|%M|%l|%m",
    ]
    if user:
        command[2:2] = ["--user", user]
    result = runner(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=slurm_environment(),
    )

    jobs: list[Job] = []
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 6:
            raise ValueError(f"invalid squeue output on line {line_number}: {line}")
        jobs.append(Job(*fields))
    return jobs


def batch_max_rss(
    job_ids: Sequence[str], runner: CommandRunner = subprocess.run
) -> dict[str, int | None]:
    """Fetch MaxRSS for all running batch steps in one sstat request."""
    usage = {job_id: None for job_id in job_ids}
    if not job_ids:
        return usage

    steps = ",".join(f"{job_id}.batch" for job_id in job_ids)
    result = runner(
        [
            "sstat",
            "--noheader",
            "--parsable2",
            "--jobs",
            steps,
            "--format",
            "JobID,MaxRSS",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=slurm_environment(),
    )
    if result.returncode != 0:
        return usage

    for line in result.stdout.splitlines():
        fields = line.split("|")
        if len(fields) < 2:
            continue
        step_id, rss = fields[0].strip(), fields[1].strip()
        if not step_id.endswith(".batch"):
            continue
        job_id = step_id.removesuffix(".batch")
        if job_id not in usage:
            continue
        try:
            usage[job_id] = parse_memory(rss)
        except ValueError:
            usage[job_id] = None
    return usage


def memory_percent(used: int | None, limit: int | None) -> str:
    if used is None or limit in {None, 0}:
        return "-"
    return f"{100 * used / limit:.0f}%"


def render(jobs: Sequence[Job], usage: dict[str, int | None]) -> str:
    rows = []
    for job in jobs:
        used = usage.get(job.job_id)
        try:
            limit = parse_memory(job.memory_limit)
        except ValueError:
            limit = None
        rows.append(
            (
                job.job_id,
                job.partition,
                job.state,
                job.elapsed,
                job.time_limit,
                format_memory(used),
                format_memory(limit),
                memory_percent(used, limit),
            )
        )

    headers = (
        "JOBID",
        "PARTITION",
        "STATE",
        "RUNTIME",
        "LIMIT",
        "MAX RSS",
        "MEM LIMIT",
        "MEM %",
    )
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]

    def line(values: Sequence[str]) -> str:
        return "  ".join(
            value.ljust(width) if index < 5 else value.rjust(width)
            for index, (value, width) in enumerate(zip(values, widths))
        )

    output = [f"Slurm queue at {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}"]
    output.append(line(headers))
    output.append(line(tuple("-" * width for width in widths)))
    output.extend(line(row) for row in rows)
    if not rows:
        output.append("No pending or running jobs.")
    return "\n".join(output)


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show pending/running Slurm jobs and batch-step peak RSS.",
        epilog="Refresh every second with: watch -n 1 -- slurm-queue-status",
    )
    parser.add_argument(
        "--user",
        help="only show jobs owned by this user (default: all visible jobs)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        jobs = list_jobs(args.user)
        running_ids = [job.job_id for job in jobs if job.state in {"R", "RUNNING"}]
        print(render(jobs, batch_max_rss(running_ids)))
    except FileNotFoundError as error:
        print(f"slurm-queue-status: command not found: {error.filename}", file=sys.stderr)
        return 2
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"slurm-queue-status: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
