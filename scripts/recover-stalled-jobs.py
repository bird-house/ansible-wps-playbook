#!/usr/bin/env python3
"""Report or recover stalled asynchronous PyWPS jobs.

The script intentionally uses only the Python standard library.  A status file
is considered stalled when it is old enough, still contains a non-terminal WPS
status, and has not changed while it is being inspected.  Recovery is never
the default: pass --recover to replace the status atomically with
ProcessFailed.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
PID_PATTERNS = (
    re.compile(
        r"Started processing request:\s*{uuid}\s+with pid:\s*(?P<pid>\d+)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:process|request)(?:ID)?[=:\s]+{uuid}.*?\bpid[=:\s]+(?P<pid>\d+)", re.IGNORECASE),
)
SLURM_ID_PATTERN = re.compile(
    r"(?:submitted\s+batch\s+job|slurm\s+job(?:\s+id)?)[=:\s]+(?P<job>[0-9]+(?:_[0-9]+)?)",
    re.IGNORECASE,
)
TERMINAL_STATES = {"ProcessSucceeded", "ProcessFailed"}
PENDING_STATES = {"ProcessAccepted", "ProcessStarted", "ProcessPaused"}
RECOVERABLE_STATES = {"ProcessStarted", "ProcessPaused"}


@dataclass
class StatusDocument:
    path: Path
    job_uuid: str
    state: str
    mtime_ns: int
    age_seconds: float
    namespaces: list[tuple[str, str]] = field(default_factory=list)
    resource_dirs: list[Path] = field(default_factory=list)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def qualified(name: str, uri: str) -> str:
    return f"{{{uri}}}{name}" if uri else name


def parse_status(path: Path, now: float, work_dir: Path | None = None) -> StatusDocument:
    job_uuid = path.stem
    if not UUID_RE.fullmatch(job_uuid):
        raise ValueError("filename is not a WPS UUID")

    namespaces: list[tuple[str, str]] = []
    for _, item in ET.iterparse(path, events=("start-ns",)):
        if item not in namespaces:
            namespaces.append(item)
    root = ET.parse(path).getroot()
    status = next((element for element in root.iter() if local_name(element.tag) == "Status"), None)
    if status is None:
        raise ValueError("missing Status element")
    states = [child for child in list(status) if local_name(child.tag).startswith("Process")]
    if len(states) != 1:
        raise ValueError("Status must contain exactly one process state")
    state = local_name(states[0].tag)
    if state not in TERMINAL_STATES | PENDING_STATES:
        raise ValueError(f"unsupported process state {state}")

    stat = path.stat()
    resources = discover_resource_dirs(root, work_dir, job_uuid)
    return StatusDocument(
        path=path,
        job_uuid=job_uuid,
        state=state,
        mtime_ns=stat.st_mtime_ns,
        age_seconds=max(0.0, now - stat.st_mtime),
        namespaces=namespaces,
        resource_dirs=resources,
    )


def discover_resource_dirs(root: ET.Element, work_dir: Path | None, job_uuid: str) -> list[Path]:
    if work_dir is None:
        return []
    base = work_dir.resolve()
    candidates: set[Path] = set()
    values: list[str] = []
    for element in root.iter():
        if element.text:
            values.append(element.text.strip())
        values.extend(element.attrib.values())
    for value in values:
        for token in re.split(r"[\s\"'<>]+", value):
            if not token.startswith("/"):
                continue
            path = Path(token.split("?", 1)[0])
            for candidate in (path, *path.parents):
                if candidate.name.startswith("pywps_process_"):
                    try:
                        resolved = candidate.resolve()
                        resolved.relative_to(base)
                    except (OSError, ValueError):
                        continue
                    candidates.add(resolved)
                    break
    direct = base / f"pywps_process_{job_uuid}"
    if direct.is_dir():
        candidates.add(direct.resolve())
    return sorted(candidates)


def find_status_files(output_dir: Path) -> Iterable[Path]:
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {output_dir}")
    for path in sorted(output_dir.glob("*.xml")):
        if UUID_RE.fullmatch(path.stem):
            yield path


def log_filesystem_diagnostics(path: Path, logger: logging.Logger) -> None:
    try:
        stat = os.statvfs(path)
        logger.info(
            "filesystem path=%s block_size=%d blocks=%d blocks_free=%d "
            "inodes=%d inodes_free=%d",
            path.resolve(),
            stat.f_frsize,
            stat.f_blocks,
            stat.f_bavail,
            stat.f_files,
            stat.f_favail,
        )
    except OSError as error:
        logger.error("filesystem path=%s diagnostics_error=%s", path, error)


def find_logged_controls(job_uuid: str, log_file: Path | None) -> tuple[set[int], set[str]]:
    pids: set[int] = set()
    slurm_ids: set[str] = set()
    if log_file is None or not log_file.exists():
        return pids, slurm_ids
    with log_file.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if job_uuid.lower() not in line.lower():
                continue
            for pattern in PID_PATTERNS:
                match = re.search(pattern.pattern.format(uuid=re.escape(job_uuid)), line, pattern.flags)
                if match:
                    pids.add(int(match.group("pid")))
            match = SLURM_ID_PATTERN.search(line)
            if match:
                slurm_ids.add(match.group("job"))
    return pids, slurm_ids


def process_is_alive(pid: int, expected_uid: int | None) -> bool:
    try:
        proc_path = Path(f"/proc/{pid}")
        if proc_path.exists() and expected_uid is not None and proc_path.stat().st_uid != expected_uid:
            return False
        os.kill(pid, 0)
    except (FileNotFoundError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def active_slurm_jobs(
    job_uuid: str,
    known_job_ids: set[str],
    user: str,
    command: str,
) -> set[str]:
    result = subprocess.run(
        [command, "--noheader", "--user", user, "--format=%i|%j|%Z"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"cannot query Slurm: {detail}")
    matches = set()
    needle = job_uuid.lower()
    for line in result.stdout.splitlines():
        fields = line.split("|", 2)
        if fields:
            job_id = fields[0].strip()
            if needle in line.lower() or job_id in known_job_ids:
                matches.add(job_id)
    return matches


def terminate_pid(pid: int, timeout: float, logger: logging.Logger) -> None:
    logger.warning("sending SIGTERM to pid=%d", pid)
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    logger.warning("pid=%d did not stop; sending SIGKILL", pid)
    os.kill(pid, signal.SIGKILL)


def cancel_slurm(job_ids: Iterable[str], command: str, logger: logging.Logger) -> None:
    for job_id in sorted(job_ids):
        logger.warning("cancelling Slurm job=%s", job_id)
        subprocess.run([command, job_id], check=True, timeout=30)


def read_pending_unchanged(document: StatusDocument) -> tuple[os.stat_result, ET.ElementTree]:
    with document.path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        contents = stream.read()
        after = os.fstat(stream.fileno())
    path_stat = document.path.stat()
    identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if (
        before.st_mtime_ns != document.mtime_ns
        or identity(before) != identity(after)
        or identity(after) != identity(path_stat)
    ):
        raise RuntimeError("status file changed during inspection")
    tree = ET.ElementTree(ET.fromstring(contents))
    root = tree.getroot()
    status = next((element for element in root.iter() if local_name(element.tag) == "Status"), None)
    states = [] if status is None else [
        child for child in list(status) if local_name(child.tag).startswith("Process")
    ]
    if len(states) != 1 or local_name(states[0].tag) not in PENDING_STATES:
        raise RuntimeError("status is no longer pending")
    return path_stat, tree


def ensure_pending_unchanged(document: StatusDocument) -> None:
    read_pending_unchanged(document)


def update_failed(document: StatusDocument, message: str) -> None:
    current_stat, tree = read_pending_unchanged(document)
    for prefix, uri in document.namespaces:
        if not re.fullmatch(r"ns\d+", prefix):
            ET.register_namespace(prefix, uri)
    root = tree.getroot()
    status = next((element for element in root.iter() if local_name(element.tag) == "Status"), None)
    if status is None:
        raise RuntimeError("Status element disappeared")
    states = [child for child in list(status) if local_name(child.tag).startswith("Process")]
    if len(states) != 1 or local_name(states[0].tag) not in PENDING_STATES:
        raise RuntimeError("status is no longer pending")
    uri = namespace(states[0].tag) or namespace(status.tag)
    ows_uri = next(
        (namespace_uri for prefix, namespace_uri in document.namespaces if prefix == "ows"),
        "http://www.opengis.net/ows/1.1",
    )
    for child in list(status):
        status.remove(child)
    failed = ET.SubElement(status, qualified("ProcessFailed", uri))
    report = ET.SubElement(
        failed,
        qualified("ExceptionReport", ows_uri),
        {"version": "1.0.0"},
    )
    exception = ET.SubElement(
        report,
        qualified("Exception", ows_uri),
        {"exceptionCode": "NoApplicableCode"},
    )
    exception_text = ET.SubElement(exception, qualified("ExceptionText", ows_uri))
    exception_text.text = message

    mode = current_stat.st_mode & 0o777
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{document.path.name}.",
        suffix=".tmp",
        dir=document.path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            tree.write(stream, encoding="utf-8", xml_declaration=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, mode)
        if os.geteuid() == 0:
            os.chown(temporary_name, current_stat.st_uid, current_stat.st_gid)
        latest_stat = document.path.stat()
        if (
            latest_stat.st_dev,
            latest_stat.st_ino,
            latest_stat.st_size,
            latest_stat.st_mtime_ns,
        ) != (
            current_stat.st_dev,
            current_stat.st_ino,
            current_stat.st_size,
            current_stat.st_mtime_ns,
        ):
            raise RuntimeError("status file changed before atomic replacement")
        os.replace(temporary_name, document.path)
        directory_fd = os.open(document.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def remove_resources(paths: Iterable[Path], work_dir: Path, logger: logging.Logger) -> None:
    base = work_dir.resolve()
    for path in paths:
        resolved = path.resolve()
        try:
            resolved.relative_to(base)
        except ValueError as error:
            raise RuntimeError(f"refusing cleanup outside {base}: {resolved}") from error
        if not resolved.name.startswith("pywps_process_"):
            raise RuntimeError(f"refusing unexpected cleanup path: {resolved}")
        if resolved.exists():
            logger.warning("removing temporary resource=%s", resolved)
            shutil.rmtree(resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--service-log", type=Path)
    parser.add_argument("--age-hours", type=float, default=24.0)
    parser.add_argument("--recover", action="store_true", help="mark confirmed stalled jobs failed")
    parser.add_argument("--terminate", action="store_true", help="terminate controls found for a stalled job")
    parser.add_argument("--cleanup", action="store_true", help="remove confidently associated work directories")
    parser.add_argument("--slurm", action="store_true", help="check Slurm for matching active jobs")
    parser.add_argument("--user", default="wps")
    parser.add_argument("--squeue-command", default="squeue")
    parser.add_argument("--scancel-command", default="scancel")
    parser.add_argument("--term-timeout", type=float, default=10.0)
    parser.add_argument("--lock-file", type=Path, default=Path("/run/lock/pywps-stalled-jobs.lock"))
    parser.add_argument("--log-file", type=Path)
    return parser


def configure_logging(log_file: Path | None) -> logging.Logger:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("pywps-stalled-jobs")


def validate_args(args: argparse.Namespace) -> None:
    if args.age_hours <= 0:
        raise ValueError("--age-hours must be greater than zero")
    if args.term_timeout < 0:
        raise ValueError("--term-timeout cannot be negative")
    if (args.terminate or args.cleanup) and not args.recover:
        raise ValueError("--terminate and --cleanup require --recover")
    if args.cleanup and args.work_dir is None:
        raise ValueError("--cleanup requires --work-dir")


def run(args: argparse.Namespace, logger: logging.Logger, now: float | None = None) -> int:
    validate_args(args)
    now = time.time() if now is None else now
    minimum_age = args.age_hours * 3600
    expected_uid = pwd.getpwnam(args.user).pw_uid
    errors = 0
    candidates = 0
    recovered = 0

    log_filesystem_diagnostics(args.output_dir, logger)
    if args.work_dir is not None:
        log_filesystem_diagnostics(args.work_dir, logger)

    for path in find_status_files(args.output_dir):
        try:
            document = parse_status(path, now, args.work_dir)
        except Exception as error:
            errors += 1
            logger.error("status=%s decision=skip reason=%s", path, error)
            continue
        if document.state in TERMINAL_STATES:
            logger.info(
                "job=%s state=%s decision=skip reason=terminal",
                document.job_uuid,
                document.state,
            )
            continue
        if document.age_seconds < minimum_age:
            logger.info(
                "job=%s state=%s age_hours=%.1f decision=skip reason=recent",
                document.job_uuid,
                document.state,
                document.age_seconds / 3600,
            )
            continue
        candidates += 1
        try:
            logged_pids, logged_slurm = find_logged_controls(document.job_uuid, args.service_log)
            live_pids = {pid for pid in logged_pids if process_is_alive(pid, expected_uid)}
            slurm_jobs: set[str] = set()
            if args.slurm:
                slurm_jobs.update(
                    active_slurm_jobs(
                        document.job_uuid,
                        logged_slurm,
                        args.user,
                        args.squeue_command,
                    )
                )
            logger.warning(
                "job=%s state=%s age_hours=%.1f decision=candidate pids=%s slurm=%s resources=%s",
                document.job_uuid,
                document.state,
                document.age_seconds / 3600,
                sorted(live_pids),
                sorted(slurm_jobs),
                [str(item) for item in document.resource_dirs],
            )
            if not args.recover:
                continue
            if document.state not in RECOVERABLE_STATES:
                logger.warning(
                    "job=%s decision=skip reason=accepted-job-may-still-be-queued",
                    document.job_uuid,
                )
                continue
            if not logged_pids:
                logger.warning(
                    "job=%s decision=skip reason=no-pywps-pid-evidence",
                    document.job_uuid,
                )
                continue
            if (live_pids or slurm_jobs) and not args.terminate:
                logger.warning(
                    "job=%s decision=skip reason=active-control-use---terminate",
                    document.job_uuid,
                )
                continue
            if args.terminate:
                ensure_pending_unchanged(document)
                for pid in sorted(live_pids):
                    terminate_pid(pid, args.term_timeout, logger)
                cancel_slurm(slurm_jobs, args.scancel_command, logger)
            message = (
                "Process failed: recovery marked this job stalled after "
                f"{document.age_seconds / 3600:.1f} hours without a status update. "
                "See the service and stalled-job recovery logs for diagnostics."
            )
            update_failed(document, message)
            if args.cleanup and args.work_dir is not None:
                remove_resources(document.resource_dirs, args.work_dir, logger)
            recovered += 1
            logger.warning("job=%s decision=recovered", document.job_uuid)
        except Exception as error:
            errors += 1
            logger.exception("job=%s decision=error reason=%s", document.job_uuid, error)
    logger.info(
        "summary candidates=%d recovered=%d errors=%d mode=%s",
        candidates,
        recovered,
        errors,
        "recover" if args.recover else "report",
    )
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging(args.log_file)
    try:
        validate_args(args)
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.info("decision=skip reason=another-run-is-active")
                return 0
            lock.write(f"{os.getpid()}\n")
            lock.flush()
            return run(args, logger)
    except (OSError, ValueError, KeyError) as error:
        logger.error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
