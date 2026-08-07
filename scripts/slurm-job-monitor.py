#!/usr/bin/env python3
"""Report Slurm queue pressure and long-running jobs."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class SlurmJob:
    job_id: str
    state: str
    elapsed_seconds: int | None


@dataclass
class Summary:
    running: int = 0
    pending: int = 0
    long_running: int = 0

    @property
    def total(self) -> int:
        return self.running + self.pending


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def parse_elapsed(value: str) -> int:
    """Convert Slurm's [[days-]hours:]minutes:seconds value to seconds."""
    text = value.strip()
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        days = int(day_text)
    parts = text.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = (int(part) for part in parts)
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = (int(part) for part in parts)
    else:
        raise ValueError(f"invalid Slurm elapsed time: {value}")
    if days < 0 or hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"invalid Slurm elapsed time: {value}")
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def list_jobs(user: str, runner: CommandRunner = subprocess.run) -> list[SlurmJob]:
    result = runner(
        [
            "squeue",
            "--noheader",
            "--user",
            user,
            "--states",
            "PENDING,RUNNING",
            "--format",
            "%i|%T|%M",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    jobs: list[SlurmJob] = []
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 3 or not all(fields):
            raise ValueError(f"invalid squeue output on line {line_number}: {line}")
        job_id, state, elapsed = fields
        if state not in {"PENDING", "RUNNING"}:
            raise ValueError(f"unexpected squeue state for job {job_id}: {state}")
        jobs.append(
            SlurmJob(
                job_id,
                state,
                parse_elapsed(elapsed) if state == "RUNNING" else None,
            )
        )
    return jobs


def inspect_jobs(
    user: str,
    long_running_seconds: float,
    pending_warning: int,
    logger: logging.Logger,
    runner: CommandRunner = subprocess.run,
) -> Summary:
    summary = Summary()
    for job in list_jobs(user, runner):
        if job.state == "PENDING":
            summary.pending += 1
            continue
        summary.running += 1
        if job.elapsed_seconds is None or job.elapsed_seconds < long_running_seconds:
            continue
        summary.long_running += 1
        logger.warning(
            "job=%s state=RUNNING finding=long-running elapsed_seconds=%d "
            "warning_seconds=%g",
            job.job_id,
            job.elapsed_seconds,
            long_running_seconds,
        )

    if summary.pending >= pending_warning:
        logger.warning(
            "finding=pending-queue-full pending=%d warning_threshold=%d",
            summary.pending,
            pending_warning,
        )
    return summary


def configure_logging(log_file: Path | None) -> logging.Logger:
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    handlers: list[logging.Handler] = [stream_handler]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        handlers.append(file_handler)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("slurm-job-monitor")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="inspect jobs owned by this user")
    parser.add_argument(
        "--long-running-minutes",
        required=True,
        type=float,
        help="warn when a running job reaches this age",
    )
    parser.add_argument(
        "--pending-warning",
        required=True,
        type=int,
        help="warn when at least this many jobs are pending",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("/run/lock/slurm-job-monitor.lock"),
    )
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args(argv)
    if args.long_running_minutes <= 0:
        parser.error("--long-running-minutes must be greater than zero")
    if args.pending_warning <= 0:
        parser.error("--pending-warning must be greater than zero")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        logger = configure_logging(args.log_file)
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open("w", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.info("result=skip reason=another-run-is-active")
                return 0
            lock.write(f"{os.getpid()}\n")
            lock.flush()
            summary = inspect_jobs(
                args.user,
                args.long_running_minutes * 60,
                args.pending_warning,
                logger,
            )
        queue_warning = summary.pending >= args.pending_warning
        log_summary = logger.warning if summary.long_running or queue_warning else logger.info
        log_summary(
            "summary running=%d pending=%d total=%d long_running=%d user=%s",
            summary.running,
            summary.pending,
            summary.total,
            summary.long_running,
            args.user,
        )
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("slurm-job-monitor").error("result=error reason=%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
