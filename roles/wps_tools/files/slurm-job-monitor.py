#!/usr/bin/env python3
"""Report Slurm queue pressure and long-running jobs."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class SlurmJob:
    job_id: str
    state: str
    elapsed_seconds: int | None


@dataclass(frozen=True)
class SlurmCapacity:
    node: str
    partition_state: str
    node_state: str
    reason: str


@dataclass
class Summary:
    running: int = 0
    pending: int = 0
    long_running: int = 0

    @property
    def total(self) -> int:
        return self.running + self.pending


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
UTC = timezone.utc
USABLE_NODE_STATES = {"allocated", "completing", "idle", "mixed"}
NODE_STATE_FLAGS = "*~#!%$@^-+"


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


def list_capacity(runner: CommandRunner = subprocess.run) -> list[SlurmCapacity]:
    result = runner(
        [
            "sinfo",
            "--noheader",
            "--Node",
            "--format",
            "%N|%a|%T|%E",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    capacity: list[SlurmCapacity] = []
    for line_number, line in enumerate(result.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 4 or not all(fields[:3]):
            raise ValueError(f"invalid sinfo output on line {line_number}: {line}")
        node, partition_state, node_state, reason = fields
        capacity.append(SlurmCapacity(node, partition_state, node_state, reason))
    return capacity


def capacity_issues(capacity: Sequence[SlurmCapacity]) -> list[str]:
    if not capacity:
        return ["reason=no-capacity-records"]
    issues: list[str] = []
    for record in capacity:
        raw_state = record.node_state.lower()
        normalized_state = raw_state.rstrip(NODE_STATE_FLAGS)
        state_flags = raw_state[len(normalized_state) :]
        partition_is_usable = record.partition_state.lower() == "up"
        node_is_responding = "*" not in state_flags
        node_is_usable = normalized_state in USABLE_NODE_STATES
        if partition_is_usable and node_is_responding and node_is_usable:
            continue
        issues.append(
            "node=%s partition_state=%s node_state=%s reason=%s"
            % (
                record.node,
                record.partition_state,
                record.node_state,
                record.reason or "none",
            )
        )
    return issues


def inspect_jobs(
    user: str,
    long_running_seconds: float,
    pending_critical: int,
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

    if summary.pending >= pending_critical:
        logger.critical(
            "finding=pending-queue-full pending=%d critical_threshold=%d",
            summary.pending,
            pending_critical,
        )
    return summary


def write_alert(
    path: Path,
    reason: str,
    *,
    pending: int | None = None,
    threshold: int | None = None,
    detail: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "status": "red",
        "checked_at": datetime.now(UTC).isoformat(),
        "reason": reason,
    }
    if pending is not None:
        payload["pending"] = pending
    if threshold is not None:
        payload["threshold"] = threshold
    if detail:
        payload["detail"] = detail
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def clear_alert(path: Path) -> None:
    path.unlink(missing_ok=True)


def configure_logging(log_file: Path | None) -> logging.Logger:
    stream_handler = logging.StreamHandler()
    # Cron mails every byte written to stdout or stderr. Keep routine findings
    # in the file log and reserve console output for actionable incidents.
    stream_handler.setLevel(logging.CRITICAL)
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
        "--pending-critical",
        dest="pending_critical",
        required=True,
        type=int,
        help="report a critical incident when at least this many jobs are pending",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("/run/lock/slurm-job-monitor.lock"),
    )
    parser.add_argument("--log-file", type=Path)
    parser.add_argument(
        "--alert-file",
        type=Path,
        default=Path("/run/pywps/slurm-red-alert.json"),
    )
    args = parser.parse_args(argv)
    if args.long_running_minutes <= 0:
        parser.error("--long-running-minutes must be greater than zero")
    if args.pending_critical <= 0:
        parser.error("--pending-critical must be greater than zero")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
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
                args.pending_critical,
                logger,
            )
            issues = capacity_issues(list_capacity())
            for issue in issues:
                logger.critical("finding=slurm-capacity-unavailable %s", issue)
        queue_critical = summary.pending >= args.pending_critical
        if issues:
            write_alert(
                args.alert_file,
                "slurm-capacity-unavailable",
                detail="; ".join(issues),
            )
        elif queue_critical:
            write_alert(
                args.alert_file,
                "pending-queue-full",
                pending=summary.pending,
                threshold=args.pending_critical,
            )
        else:
            clear_alert(args.alert_file)
        log_summary = (
            logger.warning
            if summary.long_running or queue_critical or issues
            else logger.info
        )
        log_summary(
            "summary running=%d pending=%d total=%d long_running=%d "
            "capacity_issues=%d user=%s",
            summary.running,
            summary.pending,
            summary.total,
            summary.long_running,
            len(issues),
            args.user,
        )
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("slurm-job-monitor").critical(
            "result=error reason=%s", error
        )
        if args is not None:
            try:
                write_alert(args.alert_file, "slurm-monitor-error", detail=str(error))
            except OSError as alert_error:
                logging.getLogger("slurm-job-monitor").critical(
                    "alert=red result=error reason=%s", alert_error
                )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
