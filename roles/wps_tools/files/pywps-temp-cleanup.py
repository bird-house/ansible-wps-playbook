#!/usr/bin/env python3
"""Remove aged PyWPS scheduler work directories without deleting active jobs."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import UUID


UTC = timezone.utc


@dataclass
class Summary:
    checked: int = 0
    deleted: int = 0
    protected: int = 0
    skipped: int = 0
    errors: int = 0


def read_config(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    if not path.is_file() or not config.read(path):
        raise ValueError(f"cannot read PyWPS configuration: {path}")
    return config


def job_uuid_from_dump(directory: Path) -> str | None:
    """Return the one job UUID recorded by scheduler dumps in a work directory."""
    identifiers: set[str] = set()
    for dump_path in directory.glob("job_*.dump"):
        if dump_path.is_symlink() or not dump_path.is_file():
            raise ValueError(f"unsafe scheduler dump: {dump_path}")
        with dump_path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        value = str(payload.get("process", {}).get("uuid") or "")
        try:
            identifier = str(UUID(value))
        except ValueError as error:
            raise ValueError(f"invalid job UUID in {dump_path}") from error
        identifiers.add(identifier)
    if not identifiers:
        return None
    if len(identifiers) != 1:
        raise ValueError(f"conflicting scheduler dumps in {directory}")
    return identifiers.pop()


def directory_identity(path: Path) -> tuple[int, int, int, int]:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError(f"work path is not a real directory: {path}")
    return (details.st_dev, details.st_ino, details.st_mtime_ns, details.st_ctime_ns)


def discover_aged_jobs(
    work_dir: Path,
    cutoff: datetime,
) -> tuple[list[tuple[Path, str, tuple[int, int, int, int]]], Summary]:
    jobs = []
    summary = Summary()
    cutoff_timestamp = cutoff.timestamp()
    for directory in sorted(work_dir.glob("pywps_process_*")):
        try:
            identity = directory_identity(directory)
            if directory.stat().st_mtime > cutoff_timestamp:
                continue
            summary.checked += 1
            job_uuid = job_uuid_from_dump(directory)
            if job_uuid is None:
                summary.skipped += 1
                summary.errors += 1
                print(
                    f"cannot safely associate aged work directory: {directory}",
                    file=sys.stderr,
                )
                continue
            jobs.append((directory, job_uuid, identity))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            summary.skipped += 1
            summary.errors += 1
            print(f"cannot inspect work directory {directory}: {error}", file=sys.stderr)
    return jobs, summary


def database_job_states(config_path: Path, job_uuids: set[str]) -> dict[str, bool]:
    """Return UUID-to-final-state mappings; omitted UUIDs have no database row."""
    if not job_uuids:
        return {}
    os.environ["PYWPS_CFG"] = str(config_path)
    try:
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.orm import sessionmaker
    except ImportError as error:
        raise RuntimeError(
            "temporary cleanup must run with the service Conda environment"
        ) from error

    configuration.load_configuration([str(config_path)])
    database_url = configuration.get_config_value("logging", "database")
    engine = create_engine(database_url)
    if not inspect(engine).has_table(dblog.ProcessInstance.__tablename__):
        engine.dispose()
        raise RuntimeError(
            f"PyWPS request table does not exist: {dblog.ProcessInstance.__tablename__}"
        )
    session = sessionmaker(bind=engine)()
    states: dict[str, bool] = {}
    final_states = {WPS_STATUS.SUCCEEDED, WPS_STATUS.FAILED}
    try:
        identifiers = sorted(job_uuids)
        for offset in range(0, len(identifiers), 500):
            rows = (
                session.query(dblog.ProcessInstance)
                .filter(
                    dblog.ProcessInstance.uuid.in_(
                        identifiers[offset : offset + 500]
                    )
                )
                .all()
            )
            states.update(
                (str(record.uuid), record.status in final_states) for record in rows
            )
    finally:
        session.close()
        engine.dispose()
    return states


def remove_safe_jobs(
    jobs: list[tuple[Path, str, tuple[int, int, int, int]]],
    states: dict[str, bool],
    summary: Summary,
    remover: Callable[[Path], None] = shutil.rmtree,
) -> Summary:
    for directory, job_uuid, original_identity in jobs:
        if states.get(job_uuid) is False:
            summary.protected += 1
            continue
        try:
            if directory_identity(directory) != original_identity:
                summary.skipped += 1
                summary.errors += 1
                print(f"work directory changed during cleanup: {directory}", file=sys.stderr)
                continue
            remover(directory)
            summary.deleted += 1
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            summary.errors += 1
            print(f"cannot remove work directory {directory}: {error}", file=sys.stderr)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--keep-minutes", required=True, type=float)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.keep_minutes <= 0:
        parser.error("--keep-minutes must be greater than zero")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        config = read_config(args.config)
        work_dir = Path(config.get("server", "workdir"))
        lock_file = Path(
            config.get(
                "job_control",
                "lock_file",
                fallback="/run/lock/pywps-job-control.lock",
            )
        )
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with lock_file.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            cutoff = datetime.now(UTC) - timedelta(minutes=args.keep_minutes)
            jobs, summary = discover_aged_jobs(work_dir, cutoff)
            states = database_job_states(
                args.config,
                {job_uuid for _directory, job_uuid, _identity in jobs},
            )
            remove_safe_jobs(jobs, states, summary)
        if args.verbose:
            print(
                f"checked={summary.checked} deleted={summary.deleted} "
                f"protected={summary.protected} skipped={summary.skipped} "
                f"errors={summary.errors}"
            )
        return 1 if summary.errors else 0
    except (configparser.Error, OSError, ValueError, RuntimeError) as error:
        print(f"temporary cleanup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
