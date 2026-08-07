#!/usr/bin/env python3
"""Monitor or cancel Slurm jobs that exceed a runtime limit."""

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
    elapsed_seconds: int


@dataclass
class Summary:
    checked: int = 0
    overdue: int = 0
    cancelled: int = 0
    errors: int = 0


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


def list_running_jobs(user: str, runner: CommandRunner = subprocess.run) -> list[SlurmJob]:
    result = runner(
        [
            "squeue",
            "--noheader",
            "--user",
            user,
            "--states",
            "RUNNING",
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
        if state != "RUNNING":
            raise ValueError(f"unexpected squeue state for job {job_id}: {state}")
        jobs.append(SlurmJob(job_id, state, parse_elapsed(elapsed)))
    return jobs


def cancel_job(job_id: str, runner: CommandRunner = subprocess.run) -> None:
    runner(
        ["scancel", job_id],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )


def control_jobs(
    mode: str,
    user: str,
    timeout_seconds: float,
    limit: int | None,
    logger: logging.Logger,
    runner: CommandRunner = subprocess.run,
) -> Summary:
    summary = Summary()
    for job in list_running_jobs(user, runner):
        summary.checked += 1
        if job.elapsed_seconds < timeout_seconds:
            continue
        summary.overdue += 1
        logger.warning(
            "job=%s state=%s finding=runtime-exceeded elapsed_seconds=%d "
            "timeout_seconds=%g",
            job.job_id,
            job.state,
            job.elapsed_seconds,
            timeout_seconds,
        )
        if mode == "recover":
            try:
                cancel_job(job.job_id, runner)
                summary.cancelled += 1
                logger.warning("job=%s action=cancelled", job.job_id)
            except subprocess.CalledProcessError as error:
                summary.errors += 1
                reason = (error.stderr or error.stdout or str(error)).strip()
                logger.error(
                    "job=%s action=cancel result=error reason=%s",
                    job.job_id,
                    reason,
                )
        if limit is not None and summary.overdue >= limit:
            break
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
    return logging.getLogger("slurm-job-control")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("monitor", "recover"),
        help="report overdue jobs or cancel them",
    )
    parser.add_argument("--user", required=True, help="inspect jobs owned by this user")
    parser.add_argument(
        "--timeout-hours",
        required=True,
        type=float,
        help="consider running jobs overdue at this age",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="inspect at most this many overdue jobs",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("/run/lock/slurm-job-control.lock"),
    )
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args(argv)
    if args.timeout_hours <= 0:
        parser.error("--timeout-hours must be greater than zero")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")
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
            summary = control_jobs(
                args.mode,
                args.user,
                args.timeout_hours * 3600,
                args.limit,
                logger,
            )
        log_summary = logger.error if summary.errors else logger.warning if summary.overdue else logger.info
        log_summary(
            "summary checked=%d overdue=%d cancelled=%d errors=%d mode=%s user=%s",
            summary.checked,
            summary.overdue,
            summary.cancelled,
            summary.errors,
            args.mode,
            args.user,
        )
        return 1 if summary.errors else 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("slurm-job-control").error("result=error reason=%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
