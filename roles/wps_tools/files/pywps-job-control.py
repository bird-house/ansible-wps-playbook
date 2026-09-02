#!/usr/bin/env python3
"""Control PyWPS monitoring, recovery, and statistics across storage layers."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import json
import logging
import os
import pwd
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote, urlsplit
from uuid import UUID

from wps_tools_events import JsonlEventHandler


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
FINAL_XML_STATES = {"ProcessSucceeded", "ProcessFailed"}
TIMEOUT_XML_STATES = {"ProcessStarted"}
XML_JOB_STATUSES = {
    "ProcessAccepted": "accepted",
    "ProcessStarted": "running",
    "ProcessPaused": "running",
    "ProcessSucceeded": "successful",
    "ProcessFailed": "failed",
}
SUPPORTED_LAYERS = ("xml", "database", "polling")
DEFAULT_LAYERS = {
    "monitor": SUPPORTED_LAYERS,
    "recover": SUPPORTED_LAYERS,
}
UTC = timezone.utc
XML_TIMESTAMP_TOLERANCE = timedelta(minutes=5)
MAX_DATABASE_UTC_OFFSET = timedelta(hours=14)
UUID_EPOCH_100NS = 0x01B21DD213814000
JOB_CONTROL_SECTION = "job_control"
ACCESS_LOG_RE = re.compile(
    r'^(?P<client>\S+) \S+ \S+ \[(?P<timestamp>[^]]+)\] '
    r'"(?P<method>\S+) (?P<target>\S+) (?P<protocol>[^"]+)" '
    r'(?P<status>\d{3})(?:\s|$)'
)
ACCESS_LOG_OLD_LINE_STOP_COUNT = 100
JOB_ERROR_TAIL_BYTES = 256 * 1024
ACCEPTED_RECOVERY_MESSAGE_MARKER = "accepted database request"
SLURM_OOM_RE = re.compile(
    rb"slurmstepd: error: Detected [1-9][0-9]* oom-kill event\(s\) "
    rb"in StepId=\S+\."
)


@dataclass
class Settings:
    mode: str
    layers: list[str]
    stale_after_minutes: float
    output_dir: Path | None
    pywps_config: Path | None
    lock_file: Path
    log_file: Path | None
    event_log: Path | None = None
    show_summaries: bool = False
    human_readable: bool = False
    limit: int | None = None
    output_url: str | None = None
    access_log: Path | None = None
    poll_window_minutes: float = 190
    min_poll_count: int = 3
    min_poll_duration_minutes: float = 100
    database_guard: bool = True
    repair_recovery_timestamps: bool = False
    monitor_enabled: bool = True
    recovery_enabled: bool = False
    missing_status_recovery_enabled: bool = False
    service_name: str = "unknown"
    incident_archive_enabled: bool = False
    incident_archive_dir: Path | None = None
    recovery_limit: int = 100
    missing_status_recovery_limit: int = 20
    long_running_minutes: float = 10
    database_stale_after_minutes: float = 95
    recovery_max_runtime_minutes: float = 90
    recovery_pending_timeout_minutes: float = 360
    database_accepted_stale_after_hours: float = 24
    database_status_window_hours: float = 24
    work_dir: Path | None = None
    recovery_user: str = "wps"
    recovery_group: str = "wps"
    python_executable: Path | None = None
    recovery_working_dir: Path | None = None
    database_recovery_excluded_jobs: set[str] = field(default_factory=set)


@dataclass
class LayerSummary:
    name: str
    checked: int = 0
    stalled: int = 0
    recovered: int = 0
    errors: int = 0
    stalled_jobs: set[str] = field(default_factory=set)
    status_counts: dict[str, int] | None = None
    long_running: int = 0
    long_running_jobs: set[str] = field(default_factory=set)
    recovery_blocked_jobs: set[str] = field(default_factory=set)
    recovered_jobs: set[str] = field(default_factory=set)
    repaired: int = 0


class SummaryConsoleFilter(logging.Filter):
    """Keep console output compact while allowing informational summaries."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or record.getMessage().startswith(
            "summary "
        )


class ServiceContextFilter(logging.Filter):
    """Add the service name to every record, including dependency logs."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service_name
        return True


@dataclass
class XmlStatus:
    path: Path
    job_uuid: str
    state: str
    creation_time: datetime
    modification_time: datetime
    source_identity: tuple[int, int, int, int]
    process_identifier: str
    contents: bytes

    @property
    def last_update(self) -> datetime:
        """Use the newest available update signal."""
        return max(self.creation_time, self.modification_time)


@dataclass
class MissingStatusPolls:
    job_uuid: str
    request_path: str
    first_seen: datetime
    last_seen: datetime
    count: int = 0


@dataclass
class JobDump:
    path: Path
    contents: bytes
    source_identity: tuple[int, int, int, int]
    job_uuid: str
    workdir: Path
    lineage: bool
    input_count: int


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def qualified(name: str, uri: str) -> str:
    return f"{{{uri}}}{name}" if uri else name


def parse_timestamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_xml_creation_time(
    creation_time: datetime,
    modification_time: datetime,
) -> datetime:
    """Correct PyWPS local wall-clock values that are incorrectly labelled UTC."""
    local_interpretation = creation_time.replace(tzinfo=None).astimezone(UTC)
    labelled_matches_file = (
        abs(creation_time - modification_time) <= XML_TIMESTAMP_TOLERANCE
    )
    local_matches_file = (
        abs(local_interpretation - modification_time) <= XML_TIMESTAMP_TOLERANCE
    )
    if not labelled_matches_file and local_matches_file:
        return local_interpretation
    return creation_time


def stat_identity(stat: os.stat_result) -> tuple[int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def read_xml_status(path: Path) -> tuple[XmlStatus, ET.ElementTree]:
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        contents = stream.read()
        after = os.fstat(stream.fileno())
    path_stat = path.stat()
    if (
        stat_identity(before) != stat_identity(after)
        or stat_identity(after) != stat_identity(path_stat)
    ):
        raise RuntimeError("status file changed while it was read")

    tree = ET.ElementTree(ET.fromstring(contents))
    root = tree.getroot()
    status = next((element for element in root.iter() if local_name(element.tag) == "Status"), None)
    if status is None:
        raise ValueError("missing Status element")
    creation_value = status.attrib.get("creationTime")
    if not creation_value:
        raise ValueError("missing Status creationTime")
    modification_time = datetime.fromtimestamp(path_stat.st_mtime, UTC)
    try:
        creation_time = normalize_xml_creation_time(
            parse_timestamp(creation_value),
            modification_time,
        )
    except ValueError as error:
        raise ValueError(f"invalid Status creationTime: {creation_value}") from error

    states = [child for child in list(status) if local_name(child.tag).startswith("Process")]
    if len(states) != 1:
        raise ValueError("Status must contain exactly one process state")
    process = next(
        (element for element in root.iter() if local_name(element.tag) == "Process"),
        None,
    )
    identifier = next(
        (
            element.text.strip()
            for element in process.iter()
            if local_name(element.tag) == "Identifier" and element.text
        ),
        "unknown",
    ) if process is not None else "unknown"

    return (
        XmlStatus(
            path=path,
            job_uuid=path.stem,
            state=local_name(states[0].tag),
            creation_time=creation_time,
            modification_time=modification_time,
            source_identity=stat_identity(path_stat),
            process_identifier=identifier,
            contents=contents,
        ),
        tree,
    )


def safe_filename_component(value: str, fallback: str = "unknown") -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    return (sanitized or fallback)[:80]


def failure_incident_kind(tree: ET.ElementTree) -> str:
    messages = " ".join(
        element.text or ""
        for element in tree.getroot().iter()
        if local_name(element.tag) == "ExceptionText"
    )
    if "stalled-job recovery" in messages or "repeated polling" in messages:
        return "recovered"
    return "error"


def archive_failed_xml(
    document: XmlStatus,
    tree: ET.ElementTree,
    settings: Settings,
    logger: logging.Logger,
) -> Path | None:
    if not settings.incident_archive_enabled:
        return None
    if settings.incident_archive_dir is None:
        raise ValueError("incident archiving requires incident_archive_dir")
    if document.state != "ProcessFailed":
        raise ValueError("only failed status documents can be archived")
    if stat_identity(document.path.stat()) != document.source_identity:
        raise RuntimeError("status file changed before incident archiving")

    archive_dir = settings.incident_archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = document.creation_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    kind = failure_incident_kind(tree)
    service = safe_filename_component(settings.service_name)
    process = safe_filename_component(document.process_identifier)
    destination = archive_dir / (
        f"{timestamp}__{kind}__{service}__{process}__{document.job_uuid}.xml"
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{document.job_uuid}.", suffix=".tmp", dir=archive_dir
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(document.contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o640)
        try:
            os.link(temporary_name, destination)
        except FileExistsError:
            return destination
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    logger.info(
        "layer=xml job=%s status=failed incident=%s archive=%s result=created",
        document.job_uuid,
        kind,
        destination,
    )
    return destination


def find_status_files(output_dir: Path) -> Iterable[Path]:
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {output_dir}")
    for path in sorted(output_dir.glob("*.xml")):
        if UUID_RE.fullmatch(path.stem):
            yield path


def xml_job_status(state: str) -> str:
    """Map a WPS status element to the OGC API Processes vocabulary."""
    return XML_JOB_STATUSES.get(state, "running")


def is_stalled(last_update: datetime, now: datetime, threshold: timedelta) -> bool:
    return now - last_update >= threshold


def read_job_dump(path: Path, expected_uuid: str) -> JobDump:
    if path.is_symlink():
        raise ValueError(f"job dump must not be a symlink: {path}")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        contents = stream.read()
        after = os.fstat(stream.fileno())
    path_stat = path.stat()
    if (
        stat_identity(before) != stat_identity(after)
        or stat_identity(after) != stat_identity(path_stat)
    ):
        raise RuntimeError(f"job dump changed while it was read: {path}")
    try:
        payload = json.loads(contents)
        process = payload["process"]
        request = json.loads(payload["wps_request"])
        job_uuid = str(process["uuid"])
        workdir = Path(process["workdir"]).resolve()
        inputs = request.get("inputs", {})
        if not isinstance(inputs, dict):
            raise TypeError("WPS request inputs must be a mapping")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid PyWPS job dump: {path}") from error
    if job_uuid != expected_uuid:
        raise ValueError(f"job dump UUID does not match {expected_uuid}: {path}")
    if workdir != path.parent.resolve():
        raise ValueError(f"job dump workdir does not match its directory: {path}")
    lineage = request.get("lineage")
    return JobDump(
        path=path,
        contents=contents,
        source_identity=stat_identity(path_stat),
        job_uuid=job_uuid,
        workdir=workdir,
        lineage=lineage is True or str(lineage).lower() == "true",
        input_count=sum(len(value) if isinstance(value, list) else 1 for value in inputs.values()),
    )


def find_job_dump(settings: Settings, job_uuid: str) -> JobDump:
    if settings.work_dir is None:
        raise ValueError("XML recovery requires the configured PyWPS workdir")
    matches: list[JobDump] = []
    for path in settings.work_dir.glob("pywps_process_*/job_*.dump"):
        try:
            if path.is_symlink():
                continue
            with path.open("rb") as stream:
                payload = json.load(stream)
            if str(payload.get("process", {}).get("uuid")) != job_uuid:
                continue
            matches.append(read_job_dump(path, job_uuid))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    if not matches:
        raise FileNotFoundError(f"no PyWPS job dump found for {job_uuid}")
    if len(matches) != 1:
        raise RuntimeError(f"multiple PyWPS job dumps found for {job_uuid}")
    return matches[0]


def has_slurm_oom_failure(settings: Settings, job_uuid: str) -> bool:
    """Return true only for Slurm's terminal cgroup OOM diagnostic."""
    try:
        dump = find_job_dump(settings, job_uuid)
    except FileNotFoundError:
        return False
    error_path = dump.workdir / "job-error.txt"
    if error_path.is_symlink():
        raise ValueError(f"job error output must not be a symlink: {error_path}")
    try:
        with error_path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            stream.seek(max(0, before.st_size - JOB_ERROR_TAIL_BYTES))
            contents = stream.read()
            after = os.fstat(stream.fileno())
        path_stat = error_path.stat()
    except FileNotFoundError:
        return False
    if (
        stat_identity(before) != stat_identity(after)
        or stat_identity(after) != stat_identity(path_stat)
    ):
        # The scheduler is still writing the file. Reconsider it on the next run.
        return False
    return SLURM_OOM_RE.search(contents) is not None


def archive_recovery_sources(
    document: XmlStatus,
    dump: JobDump,
    settings: Settings,
) -> tuple[Path, Path]:
    if not settings.incident_archive_enabled or settings.incident_archive_dir is None:
        raise ValueError("dump-backed XML recovery requires incident archiving")
    if stat_identity(document.path.stat()) != document.source_identity:
        raise RuntimeError("status file changed before recovery archiving")
    if stat_identity(dump.path.stat()) != dump.source_identity:
        raise RuntimeError("job dump changed before recovery archiving")
    archive_dir = settings.incident_archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = document.creation_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = "__".join(
        (
            timestamp,
            "source",
            safe_filename_component(settings.service_name),
            safe_filename_component(document.process_identifier),
            document.job_uuid,
        )
    )

    optional_logs: dict[str, bytes] = {}
    for filename in ("job-error.txt", "job-output.txt"):
        source = dump.workdir / filename
        if source.is_symlink():
            raise ValueError(f"job log must not be a symlink: {source}")
        try:
            with source.open("rb") as stream:
                before = os.fstat(stream.fileno())
                contents = stream.read()
                after = os.fstat(stream.fileno())
        except FileNotFoundError:
            continue
        try:
            source_stat = source.stat()
        except FileNotFoundError as error:
            raise RuntimeError(f"job log changed while it was read: {source}") from error
        if (
            stat_identity(before) != stat_identity(after)
            or stat_identity(after) != stat_identity(source_stat)
        ):
            raise RuntimeError(f"job log changed while it was read: {source}")
        optional_logs[filename] = contents

    def archive_bytes(destination: Path, contents: bytes) -> Path:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{document.job_uuid}.", suffix=".tmp", dir=archive_dir
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o640)
            try:
                os.link(temporary_name, destination)
            except FileExistsError:
                if destination.read_bytes() != contents:
                    raise RuntimeError(f"recovery archive collision: {destination}")
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        return destination

    xml_archive = archive_bytes(archive_dir / f"{stem}.xml", document.contents)
    dump_archive = archive_bytes(archive_dir / f"{stem}.dump", dump.contents)
    for filename, contents in optional_logs.items():
        archive_bytes(archive_dir / f"{stem}.{filename}", contents)
    return xml_archive, dump_archive


def update_from_job_dump(
    dump: JobDump,
    document: XmlStatus,
    settings: Settings,
    message: str,
) -> None:
    if settings.pywps_config is None:
        raise ValueError("dump-backed XML recovery requires pywps_config")
    if settings.python_executable is None:
        raise ValueError("dump-backed XML recovery requires the Conda Python path")
    command = [
        str(settings.python_executable),
        str(Path(__file__).resolve()),
        "--recover-job-dump",
        str(settings.pywps_config),
        str(dump.path),
        document.job_uuid,
        str(document.path),
        message,
    ]
    run_options: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 120,
        "check": False,
    }
    if settings.recovery_working_dir is not None:
        run_options["cwd"] = settings.recovery_working_dir
    if os.geteuid() == 0:
        account = pwd.getpwnam(settings.recovery_user)
        home = Path(account.pw_dir)
        child_environment = os.environ.copy()
        child_environment.update(
            {
                "HOME": str(home),
                "USER": settings.recovery_user,
                "LOGNAME": settings.recovery_user,
                "PYTHONUSERBASE": str(home / ".local"),
                "PYTHONNOUSERSITE": "1",
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
            }
        )
        child_environment.pop("XDG_RUNTIME_DIR", None)
        run_options.update(
            user=settings.recovery_user,
            group=settings.recovery_group,
            extra_groups=[],
            env=child_environment,
        )
    completed = subprocess.run(command, **run_options)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"PyWPS dump recovery failed: {detail}")
    recovered, tree = read_xml_status(document.path)
    if recovered.state != "ProcessFailed":
        raise RuntimeError("PyWPS dump recovery did not create ProcessFailed XML")
    if dump.lineage and dump.input_count and not any(
        local_name(element.tag) == "DataInputs" for element in tree.getroot().iter()
    ):
        raise RuntimeError("PyWPS dump recovery omitted requested input lineage")


def recover_stalled_xml(
    document: XmlStatus,
    settings: Settings,
    message: str,
) -> None:
    dump = find_job_dump(settings, document.job_uuid)
    archive_recovery_sources(document, dump, settings)
    if stat_identity(document.path.stat()) != document.source_identity:
        raise RuntimeError("status file changed before dump-backed recovery")
    if stat_identity(dump.path.stat()) != dump.source_identity:
        raise RuntimeError("job dump changed before dump-backed recovery")
    update_from_job_dump(dump, document, settings, message)


def run_xml_layer(
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
) -> LayerSummary:
    summary = LayerSummary("xml")
    if settings.output_dir is None:
        raise ValueError("the XML layer requires output_dir")
    threshold = timedelta(minutes=settings.stale_after_minutes)
    long_running_threshold = timedelta(minutes=settings.long_running_minutes)
    for path in find_status_files(settings.output_dir):
        summary.checked += 1
        try:
            document, tree = read_xml_status(path)
            if document.state in FINAL_XML_STATES:
                if document.state == "ProcessFailed":
                    archive_failed_xml(document, tree, settings, logger)
                logger.debug(
                    "layer=xml job=%s status=%s finding=final",
                    document.job_uuid,
                    xml_job_status(document.state),
                )
                continue
            if document.state not in TIMEOUT_XML_STATES:
                logger.debug(
                    "layer=xml job=%s status=%s decision=skip-timeout "
                    "reason=job-not-running",
                    document.job_uuid,
                    xml_job_status(document.state),
                )
                continue
            check_job_error = document.state == "ProcessStarted" and is_stalled(
                document.last_update, now, long_running_threshold
            )
            slurm_oom = check_job_error and has_slurm_oom_failure(
                settings, document.job_uuid
            )
            if not slurm_oom and not is_stalled(document.last_update, now, threshold):
                logger.debug(
                    "layer=xml job=%s status=%s finding=recent creation=%s mtime=%s",
                    document.job_uuid,
                    xml_job_status(document.state),
                    document.creation_time.isoformat(),
                    document.modification_time.isoformat(),
                )
                continue
            summary.stalled += 1
            summary.stalled_jobs.add(document.job_uuid)
            if slurm_oom:
                logger.warning(
                    "layer=xml job=%s status=%s finding=slurm-oom error=job-error.txt",
                    document.job_uuid,
                    xml_job_status(document.state),
                )
            else:
                logger.info(
                    "layer=xml job=%s status=%s finding=stalled updated=%s",
                    document.job_uuid,
                    xml_job_status(document.state),
                    document.last_update.isoformat(),
                )
            if settings.mode == "recover":
                if slurm_oom:
                    message = (
                        "Process failed: stalled-job recovery detected that Slurm "
                        "terminated the worker after it exceeded its memory allocation."
                    )
                else:
                    message = (
                        "Process failed: stalled-job recovery found no status update "
                        f"for at least {settings.stale_after_minutes:g} minutes."
                    )
                recover_stalled_xml(document, settings, message)
                recovered_document, recovered_tree = read_xml_status(path)
                archive_failed_xml(recovered_document, recovered_tree, settings, logger)
                summary.recovered += 1
                summary.recovered_jobs.add(document.job_uuid)
                logger.warning(
                    "layer=xml job=%s status=failed action=recovered",
                    document.job_uuid,
                )
            if settings.limit is not None and summary.stalled >= settings.limit:
                break
        except Exception as error:
            if (
                isinstance(error, FileNotFoundError)
                and error.filename is not None
                and Path(error.filename) == path
            ):
                # Output retention may remove a status after the directory scan.
                logger.debug(
                    "layer=xml file=%s decision=skip reason=status-disappeared",
                    path,
                )
                continue
            summary.errors += 1
            if settings.mode == "recover" and UUID_RE.fullmatch(path.stem):
                summary.recovery_blocked_jobs.add(path.stem)
            logger.critical(
                "layer=xml file=%s result=error reason=%s",
                path,
                error,
                exc_info=True,
            )
    return summary


def parse_access_log_line(line: str) -> tuple[str, datetime, str, str, int] | None:
    match = ACCESS_LOG_RE.match(line)
    if match is None:
        return None
    try:
        timestamp = datetime.strptime(
            match.group("timestamp"), "%d/%b/%Y:%H:%M:%S %z"
        ).astimezone(UTC)
    except ValueError:
        return None
    return (
        match.group("client"),
        timestamp,
        match.group("method"),
        match.group("target"),
        int(match.group("status")),
    )


def iter_lines_reverse(path: Path, block_size: int = 64 * 1024) -> Iterable[str]:
    """Read a potentially large log from newest to oldest without loading it."""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        position = stream.tell()
        remainder = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            stream.seek(position)
            chunk = stream.read(read_size)
            parts = (chunk + remainder).split(b"\n")
            remainder = parts[0]
            for line in reversed(parts[1:]):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if remainder:
            yield remainder.decode("utf-8", errors="replace")


def find_missing_status_polls(settings: Settings, now: datetime) -> list[MissingStatusPolls]:
    if settings.access_log is None:
        raise ValueError("the polling layer requires access_log")
    if settings.output_url is None:
        raise ValueError("the polling layer requires output_url")

    output_path = unquote(urlsplit(settings.output_url).path).rstrip("/")
    if not output_path.startswith("/"):
        raise ValueError("output_url must contain an absolute URL path")
    status_path_re = re.compile(
        rf"^{re.escape(output_path)}/(?P<uuid>[0-9a-fA-F-]{{36}})\.xml$"
    )
    window_start = now - timedelta(minutes=settings.poll_window_minutes)
    future_limit = now + timedelta(minutes=5)
    if not settings.access_log.is_file():
        raise FileNotFoundError(f"access log does not exist: {settings.access_log}")

    polls: dict[str, MissingStatusPolls] = {}
    consecutive_old_lines = 0
    for line in iter_lines_reverse(settings.access_log):
        parsed = parse_access_log_line(line)
        if parsed is None:
            continue
        _, timestamp, method, target, status = parsed
        if timestamp < window_start:
            consecutive_old_lines += 1
            if consecutive_old_lines >= ACCESS_LOG_OLD_LINE_STOP_COUNT:
                break
            continue
        consecutive_old_lines = 0
        if timestamp > future_limit:
            continue
        if method not in {"GET", "HEAD"} or status != 404:
            continue
        request_path = unquote(urlsplit(target).path)
        match = status_path_re.fullmatch(request_path)
        if match is None:
            continue
        job_uuid = match.group("uuid")
        if UUID_RE.fullmatch(job_uuid) is None:
            continue
        candidate = polls.get(job_uuid)
        if candidate is None:
            polls[job_uuid] = MissingStatusPolls(
                job_uuid=job_uuid,
                request_path=request_path,
                first_seen=timestamp,
                last_seen=timestamp,
                count=1,
            )
        else:
            candidate.first_seen = min(candidate.first_seen, timestamp)
            candidate.last_seen = max(candidate.last_seen, timestamp)
            candidate.count += 1

    minimum_duration = timedelta(minutes=settings.min_poll_duration_minutes)
    return sorted(
        (
            candidate
            for candidate in polls.values()
            if candidate.count >= settings.min_poll_count
            and candidate.last_seen - candidate.first_seen >= minimum_duration
        ),
        key=lambda candidate: (candidate.first_seen, candidate.job_uuid),
    )


def missing_status_xml(candidate: MissingStatusPolls, output_url: str, now: datetime) -> bytes:
    ET.register_namespace("wps", "http://www.opengis.net/wps/1.0.0")
    ET.register_namespace("ows", "http://www.opengis.net/ows/1.1")
    wps_uri = "http://www.opengis.net/wps/1.0.0"
    ows_uri = "http://www.opengis.net/ows/1.1"
    status_location = f"{output_url.rstrip('/')}/{candidate.job_uuid}.xml"
    root = ET.Element(
        qualified("ExecuteResponse", wps_uri),
        {
            "service": "WPS",
            "version": "1.0.0",
            "statusLocation": status_location,
        },
    )
    process = ET.SubElement(
        root,
        qualified("Process", wps_uri),
        {qualified("processVersion", wps_uri): "unknown"},
    )
    ET.SubElement(process, qualified("Identifier", ows_uri)).text = "unknown"
    ET.SubElement(process, qualified("Title", ows_uri)).text = "Recovered missing status document"
    status = ET.SubElement(
        root,
        qualified("Status", wps_uri),
        {"creationTime": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
    )
    failed = ET.SubElement(status, qualified("ProcessFailed", wps_uri))
    report = ET.SubElement(failed, qualified("ExceptionReport", wps_uri))
    exception = ET.SubElement(
        report,
        qualified("Exception", ows_uri),
        {"exceptionCode": "NoApplicableCode", "locator": "None"},
    )
    ET.SubElement(exception, qualified("ExceptionText", ows_uri)).text = (
        "Process failed: repeated polling found that this PyWPS status document "
        "was not created. The request can no longer be monitored."
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def create_missing_status_file(path: Path, contents: bytes) -> bool:
    """Atomically create a status file, without replacing a concurrent writer."""
    directory_stat = path.parent.stat()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o644)
        if os.geteuid() == 0:
            os.chown(temporary_name, directory_stat.st_uid, directory_stat.st_gid)
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def database_recovery_vetoes(
    settings: Settings,
    job_uuids: list[str],
) -> dict[str, str]:
    """Protect every request whose database state is not final."""
    if not settings.database_guard:
        return {}
    if settings.pywps_config is None:
        raise ValueError("the polling database guard requires pywps_config")

    os.environ["PYWPS_CFG"] = str(settings.pywps_config)
    try:
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.orm import sessionmaker
    except ImportError as error:
        raise RuntimeError(
            "the polling database guard must run with the service Conda environment"
        ) from error

    configuration.load_configuration([str(settings.pywps_config)])
    database_url = configuration.get_config_value("logging", "database")
    engine = create_engine(database_url)
    if not inspect(engine).has_table(dblog.ProcessInstance.__tablename__):
        engine.dispose()
        raise RuntimeError(
            f"PyWPS request table does not exist: {dblog.ProcessInstance.__tablename__}"
        )
    final_statuses = {WPS_STATUS.SUCCEEDED, WPS_STATUS.FAILED}
    vetoes: dict[str, str] = {}
    session = sessionmaker(bind=engine)()
    try:
        records = (
            session.query(dblog.ProcessInstance)
            .filter(dblog.ProcessInstance.uuid.in_(job_uuids))
            .all()
        )
        for record in records:
            if record.status in final_statuses:
                continue
            vetoes[record.uuid] = "database-request-is-nonfinal"
    finally:
        session.close()
        engine.dispose()
    return vetoes


def run_polling_layer(
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
) -> LayerSummary:
    summary = LayerSummary("polling")
    if settings.output_dir is None:
        raise ValueError("the polling layer requires output_dir")
    if not settings.output_dir.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {settings.output_dir}")
    candidates = find_missing_status_polls(settings, now)
    if not candidates:
        return summary
    vetoes = database_recovery_vetoes(
        settings,
        [candidate.job_uuid for candidate in candidates],
    )
    for candidate in candidates:
        summary.checked += 1
        try:
            if candidate.job_uuid in vetoes:
                logger.info(
                    "layer=polling job=%s decision=skip reason=%s polls=%d",
                    candidate.job_uuid,
                    vetoes[candidate.job_uuid],
                    candidate.count,
                )
                continue
            status_path = settings.output_dir / f"{candidate.job_uuid}.xml"
            if status_path.exists():
                logger.info(
                    "layer=polling job=%s decision=status-exists polls=%d",
                    candidate.job_uuid,
                    candidate.count,
                )
                continue
            summary.stalled += 1
            summary.stalled_jobs.add(candidate.job_uuid)
            logger.info(
                "layer=polling job=%s decision=missing-status polls=%d "
                "first_seen=%s last_seen=%s",
                candidate.job_uuid,
                candidate.count,
                candidate.first_seen.isoformat(),
                candidate.last_seen.isoformat(),
            )
            if settings.mode == "recover":
                contents = missing_status_xml(candidate, settings.output_url or "", now)
                if create_missing_status_file(status_path, contents):
                    recovered_document, recovered_tree = read_xml_status(status_path)
                    archive_failed_xml(recovered_document, recovered_tree, settings, logger)
                    summary.recovered += 1
                    summary.recovered_jobs.add(candidate.job_uuid)
                    logger.warning(
                        "layer=polling job=%s status=failed action=recovered polls=%d",
                        candidate.job_uuid,
                        candidate.count,
                    )
                else:
                    logger.info(
                        "layer=polling job=%s decision=concurrent-status-create",
                        candidate.job_uuid,
                    )
        except Exception as error:
            summary.errors += 1
            logger.critical(
                "layer=polling job=%s decision=error reason=%s",
                candidate.job_uuid,
                error,
                exc_info=True,
            )
        if settings.limit is not None and summary.stalled >= settings.limit:
            break
    return summary


def uuid1_timestamp(value: object) -> datetime | None:
    """Return the UTC creation time encoded by a version-1 UUID."""
    try:
        identifier = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None
    if identifier.version != 1:
        return None
    seconds = (identifier.time - UUID_EPOCH_100NS) / 10_000_000
    return datetime.fromtimestamp(seconds, UTC)


def database_wall_clock_offset(record: object) -> timedelta | None:
    """Infer the offset used for naive PyWPS timestamps from its v1 UUID."""
    value = getattr(record, "time_start", None)
    identifier_time = uuid1_timestamp(getattr(record, "uuid", None))
    if value is None or value.tzinfo is not None or identifier_time is None:
        return None
    difference = value.replace(tzinfo=UTC) - identifier_time
    offset = timedelta(minutes=round(difference.total_seconds() / 60))
    if abs(offset) > MAX_DATABASE_UTC_OFFSET:
        return None
    if abs(difference - offset) > XML_TIMESTAMP_TOLERANCE:
        return None
    return offset


def database_timestamp(record: object, value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    offset = database_wall_clock_offset(record)
    if offset is not None:
        return value.replace(tzinfo=UTC) - offset
    # Older/non-v1 rows cannot identify their writer's timezone. Preserve the
    # established fallback to the monitor host's local timezone.
    return value.astimezone(UTC)


def database_last_update(record: object) -> datetime:
    value = getattr(record, "time_end", None) or getattr(record, "time_start", None)
    if value is None:
        raise ValueError("database record has no start or update time")
    return database_timestamp(record, value)


def database_start_time(record: object) -> datetime | None:
    value = getattr(record, "time_start", None)
    if value is None:
        return None
    return database_timestamp(record, value)


def database_naive_cutoff(now: datetime, threshold: timedelta) -> datetime:
    """Return a naive cutoff in the local wall-clock convention used by PyWPS."""
    return (now - threshold).astimezone().replace(tzinfo=None)


def database_candidate_cutoff(now: datetime, threshold: timedelta) -> datetime:
    """Return a cutoff covering naive wall clocks in every valid UTC offset."""
    return (now - threshold + MAX_DATABASE_UTC_OFFSET).astimezone(UTC).replace(
        tzinfo=None
    )


def database_naive_now(now: datetime) -> datetime:
    """Return an aware instant as the naive local wall clock stored by PyWPS."""
    return now.astimezone().replace(tzinfo=None)


def database_recovery_end_time(
    record: object,
    now: datetime,
    max_runtime: timedelta,
) -> datetime:
    """Return a bounded end time in the database writer's time convention."""
    raw_start = getattr(record, "time_start", None)
    started = database_start_time(record)
    if raw_start is None or started is None:
        raise ValueError("database record has no start time")
    bounded_end = min(now, started + max_runtime)
    return raw_start + (bounded_end - started)


def database_recovery_timeout(record: object, settings: Settings) -> timedelta:
    """Choose the runtime or pending lifetime recorded by the recovery message."""
    message = str(getattr(record, "message", "") or "")
    minutes = (
        settings.recovery_pending_timeout_minutes
        if ACCEPTED_RECOVERY_MESSAGE_MARKER in message
        else settings.recovery_max_runtime_minutes
    )
    return timedelta(minutes=minutes)


def repair_database_recovery_timestamps(
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
    job_uuids: set[str] | None = None,
) -> LayerSummary:
    """Shorten excessive runtimes recorded for failed database jobs."""
    summary = LayerSummary("timestamps")
    if settings.pywps_config is None:
        raise ValueError("timestamp repair requires pywps_config")
    if job_uuids is not None and not job_uuids:
        return summary

    os.environ["PYWPS_CFG"] = str(settings.pywps_config)
    try:
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.orm import sessionmaker
    except ImportError as error:
        raise RuntimeError(
            "timestamp repair must run with the service Conda environment"
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
            dblog.ProcessInstance.status == WPS_STATUS.FAILED,
            dblog.ProcessInstance.time_start.isnot(None),
            dblog.ProcessInstance.time_end.isnot(None),
        )
        if job_uuids is not None:
            query = query.filter(dblog.ProcessInstance.uuid.in_(sorted(job_uuids)))
        records = query.order_by(
            dblog.ProcessInstance.time_start,
            dblog.ProcessInstance.uuid,
        ).all()
        for record in records:
            if settings.limit is not None and summary.repaired >= settings.limit:
                break
            summary.checked += 1
            try:
                repaired_end = database_recovery_end_time(
                    record,
                    now,
                    database_recovery_timeout(record, settings),
                )
                current_end = database_timestamp(record, record.time_end)
                proposed_end = database_timestamp(record, repaired_end)
                if current_end <= proposed_end:
                    continue
                record.time_end = repaired_end
                session.commit()
                summary.repaired += 1
                logger.warning(
                    "layer=timestamps job=%s action=repaired old_end=%s new_end=%s",
                    record.uuid,
                    current_end.isoformat(),
                    proposed_end.isoformat(),
                )
            except Exception as error:
                session.rollback()
                summary.errors += 1
                logger.critical(
                    "layer=timestamps job=%s decision=error reason=%s",
                    getattr(record, "uuid", "unknown"),
                    error,
                    exc_info=True,
                )
    finally:
        session.close()
        engine.dispose()
    return summary


def is_database_job_long_running(
    record: object,
    now: datetime,
    threshold: timedelta,
) -> bool:
    started = database_start_time(record)
    return started is not None and is_stalled(started, now, threshold)


def classify_database_job(
    record: object,
    now: datetime,
    long_running_threshold: timedelta,
    stale_threshold: timedelta,
) -> str | None:
    if is_stalled(database_last_update(record), now, stale_threshold):
        return "stalled"
    if is_database_job_long_running(record, now, long_running_threshold):
        return "long-running"
    return None


def database_job_status(value: object, wps_status: object) -> str | None:
    """Map a PyWPS database status to the OGC API Processes vocabulary."""
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
    return None


def database_status_can_timeout(value: object, wps_status: object) -> bool:
    """Return whether a PyWPS status represents active execution."""
    return value == getattr(wps_status, "STARTED", object())


def summarize_database_statuses(
    rows: Iterable[tuple[object, int]], wps_status: object
) -> dict[str, int]:
    result = {
        "total": 0,
        "accepted": 0,
        "running": 0,
        "successful": 0,
        "failed": 0,
        "dismissed": 0,
        "unmapped": 0,
    }
    for value, count in rows:
        result["total"] += count
        status = database_job_status(value, wps_status)
        result[status or "unmapped"] += count
    return result


def summarize_recent_database_statuses(
    records: Iterable[object],
    wps_status: object,
    now: datetime,
    window: timedelta,
) -> dict[str, int]:
    """Summarize records whose normalized start time is inside the window."""
    counts: dict[object, int] = {}
    cutoff = now - window
    for record in records:
        started = database_start_time(record)
        if started is None or started < cutoff or started > now:
            continue
        counts[record.status] = counts.get(record.status, 0) + 1
    return summarize_database_statuses(counts.items(), wps_status)


def run_database_layer(
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
) -> LayerSummary:
    summary = LayerSummary("database")
    if settings.pywps_config is None:
        raise ValueError("the database layer requires pywps_config")

    os.environ["PYWPS_CFG"] = str(settings.pywps_config)
    try:
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS
        from sqlalchemy import and_, create_engine, func, inspect, or_
        from sqlalchemy.orm import sessionmaker
    except ImportError as error:
        raise RuntimeError(
            "the database layer must run with the service Conda environment"
        ) from error

    threshold = timedelta(minutes=settings.database_stale_after_minutes)
    accepted_threshold = timedelta(
        hours=settings.database_accepted_stale_after_hours
    )
    long_running_threshold = timedelta(minutes=settings.long_running_minutes)
    status_window = timedelta(hours=settings.database_status_window_hours)
    database_url = configuration.get_config_value("logging", "database")
    engine = create_engine(database_url)
    if not inspect(engine).has_table(dblog.ProcessInstance.__tablename__):
        engine.dispose()
        raise RuntimeError(
            f"PyWPS request table does not exist: {dblog.ProcessInstance.__tablename__}"
        )
    session = sessionmaker(bind=engine)()
    try:
        if settings.mode == "monitor":
            # Fetch a timezone-safe superset, then apply the exact UTC window
            # after normalizing each PyWPS timestamp.
            oldest_candidate = (
                now - status_window - MAX_DATABASE_UTC_OFFSET
            ).replace(tzinfo=None)
            newest_candidate = (now + MAX_DATABASE_UTC_OFFSET).replace(tzinfo=None)
            recent_records = session.query(dblog.ProcessInstance).filter(
                dblog.ProcessInstance.time_start >= oldest_candidate,
                dblog.ProcessInstance.time_start <= newest_candidate,
            ).all()
            status_counts = summarize_recent_database_statuses(
                recent_records,
                WPS_STATUS,
                now,
                status_window,
            )
            summary.status_counts = status_counts
            summary.checked = status_counts["total"]
        # Queue wait is not execution time. STARTED rows use the normal runtime
        # threshold. ACCEPTED rows use a much longer threshold so abandoned
        # queue records are eventually reconciled. PAUSED and unknown rows are
        # visible in status aggregates but are never recovered automatically.
        query = session.query(dblog.ProcessInstance)
        if settings.mode == "recover" and settings.database_recovery_excluded_jobs:
            query = query.filter(
                ~dblog.ProcessInstance.uuid.in_(
                    sorted(settings.database_recovery_excluded_jobs)
                )
            )
        # The writer and monitor may use different local timezones. Select a
        # safe superset in SQL, then classify each row against UTC in Python.
        cutoff = database_candidate_cutoff(now, threshold)
        long_running_cutoff = database_candidate_cutoff(
            now, long_running_threshold
        )
        accepted_cutoff = database_candidate_cutoff(now, accepted_threshold)
        last_update = func.coalesce(
            dblog.ProcessInstance.time_end,
            dblog.ProcessInstance.time_start,
        )
        stale_filter = or_(last_update.is_(None), last_update <= cutoff)
        accepted_stale_filter = or_(
            last_update.is_(None),
            last_update <= accepted_cutoff,
        )
        started_candidate = and_(
            dblog.ProcessInstance.status == WPS_STATUS.STARTED,
            stale_filter
            if settings.mode == "recover"
            else or_(
                stale_filter,
                dblog.ProcessInstance.time_start <= long_running_cutoff,
            ),
        )
        accepted_candidate = and_(
            dblog.ProcessInstance.status == WPS_STATUS.ACCEPTED,
            accepted_stale_filter,
        )
        query = query.filter(or_(started_candidate, accepted_candidate))
        query = query.order_by(last_update, dblog.ProcessInstance.uuid)
        if settings.mode != "recover" and settings.limit is not None:
            query = query.limit(settings.limit)
        records = query.all()
        for record in records:
            if settings.mode != "monitor":
                summary.checked += 1
            try:
                is_started = database_status_can_timeout(record.status, WPS_STATUS)
                is_accepted = record.status == WPS_STATUS.ACCEPTED
                if not (is_started or is_accepted):
                    logger.debug(
                        "layer=database job=%s status=%s decision=skip-timeout "
                        "reason=job-not-running",
                        record.uuid,
                        database_job_status(record.status, WPS_STATUS) or "unmapped",
                    )
                    continue
                last_update = database_last_update(record)
                started = database_start_time(record)
                if is_accepted:
                    finding = (
                        "stalled"
                        if is_stalled(last_update, now, accepted_threshold)
                        else None
                    )
                else:
                    finding = classify_database_job(
                        record,
                        now,
                        long_running_threshold,
                        threshold,
                    )
                if settings.mode != "recover" and finding == "long-running":
                    summary.long_running += 1
                    summary.long_running_jobs.add(str(record.uuid))
                    logger.info(
                        "layer=database job=%s status=%s finding=long-running "
                        "started=%s elapsed_minutes=%d",
                        record.uuid,
                        database_job_status(record.status, WPS_STATUS) or "unmapped",
                        started.isoformat(),
                        int((now - started).total_seconds() // 60),
                    )
                if finding != "stalled":
                    logger.debug(
                        "layer=database job=%s status=%s finding=recent updated=%s",
                        record.uuid,
                        database_job_status(record.status, WPS_STATUS) or "unmapped",
                        last_update.isoformat(),
                    )
                    continue
                summary.stalled += 1
                summary.stalled_jobs.add(str(record.uuid))
                logger.info(
                    "layer=database job=%s status=%s finding=stalled updated=%s",
                    record.uuid,
                    database_job_status(record.status, WPS_STATUS) or "unmapped",
                    last_update.isoformat(),
                )
                if settings.mode == "recover":
                    record.status = WPS_STATUS.FAILED
                    record.percent_done = 100
                    if is_accepted:
                        record.message = (
                            "Process failed: stalled-job recovery found an accepted "
                            "database request that did not advance for at least "
                            f"{settings.database_accepted_stale_after_hours:g} hours."
                        )
                    else:
                        record.message = (
                            "Process failed: stalled-job recovery found no database "
                            "update for at least "
                            f"{settings.database_stale_after_minutes:g} minutes."
                        )
                    record.time_end = database_recovery_end_time(
                        record,
                        now,
                        timedelta(
                            minutes=(
                                settings.recovery_pending_timeout_minutes
                                if is_accepted
                                else settings.recovery_max_runtime_minutes
                            )
                        ),
                    )
                    session.query(dblog.RequestInstance).filter_by(
                        uuid=record.uuid
                    ).delete()
                    session.commit()
                    summary.recovered += 1
                    summary.recovered_jobs.add(str(record.uuid))
                    logger.warning(
                        "layer=database job=%s status=failed action=recovered",
                        record.uuid,
                    )
                    if settings.limit is not None and summary.stalled >= settings.limit:
                        break
            except Exception as error:
                session.rollback()
                summary.errors += 1
                logger.critical(
                    "layer=database job=%s decision=error reason=%s",
                    getattr(record, "uuid", "unknown"),
                    error,
                    exc_info=True,
                )
    finally:
        session.close()
        engine.dispose()
    return summary


def read_config(path: Path | None) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if path is None:
        return parser
    if not parser.read(path):
        raise FileNotFoundError(f"configuration file does not exist: {path}")
    if not parser.has_section(JOB_CONTROL_SECTION):
        raise ValueError(f"missing [job_control] section in {path}")
    return parser


def optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def related_log_file(
    config: configparser.ConfigParser, label: str
) -> Path | None:
    pywps_log_file = optional_path(config.get("logging", "file", fallback=None))
    if pywps_log_file is None:
        return None
    return pywps_log_file.with_name(
        f"{pywps_log_file.stem}-{label}{pywps_log_file.suffix}"
    )


def job_monitor_log_file(config: configparser.ConfigParser) -> Path | None:
    return related_log_file(config, "job-monitor")


def job_event_log_file(config: configparser.ConfigParser) -> Path | None:
    pywps_log_file = optional_path(config.get("logging", "file", fallback=None))
    if pywps_log_file is None:
        return None
    return pywps_log_file.with_name(f"{pywps_log_file.stem}-events.jsonl")


def validate_python_runtime(expected: Path | None) -> None:
    if expected is None:
        return
    try:
        matches = os.path.samefile(expected, sys.executable)
    except OSError as error:
        raise ValueError(f"configured Conda Python is unavailable: {expected}") from error
    if not matches:
        raise ValueError(
            f"job control must run with {expected}, not {sys.executable}"
        )


def parse_args(argv: list[str] | None = None) -> Settings:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--config",
        type=Path,
        help="service PyWPS configuration containing paths and defaults",
    )
    preliminary, _ = pre_parser.parse_known_args(argv)
    config = read_config(preliminary.config)
    if not config.has_section(JOB_CONTROL_SECTION):
        config.add_section(JOB_CONTROL_SECTION)
    control_config = config[JOB_CONTROL_SECTION]

    parser = argparse.ArgumentParser(
        description=__doc__,
        parents=[pre_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s --config /etc/pywps/rook.cfg monitor
  %(prog)s --config /etc/pywps/rook.cfg monitor --layer polling
  %(prog)s --config /etc/pywps/rook.cfg recover --layer xml
""",
    )
    parser.add_argument(
        "mode",
        metavar="{monitor,recover}",
        choices=("monitor", "recover"),
        help="monitor without changes or recover stalled jobs as failed",
    )
    parser.add_argument(
        "--layer",
        action="append",
        choices=SUPPORTED_LAYERS,
        metavar="{xml,database,polling}",
        help="select xml, database, or polling; repeat for multiple layers",
    )
    console_output = parser.add_mutually_exclusive_group()
    console_output.add_argument(
        "--show-summaries",
        action="store_true",
        help="write structured layer summaries to the console",
    )
    console_output.add_argument(
        "--human-readable",
        action="store_true",
        help="write a compact operator report to the console",
    )
    parser.add_argument(
        "--long-running-minutes",
        type=float,
        default=float(control_config.get("long_running_minutes", "10")),
        help="warn about started database jobs running this many minutes",
    )
    parser.add_argument(
        "--stale-after-minutes",
        type=float,
        default=float(control_config.get("stale_after_minutes", "95")),
        help="consider started XML jobs stalled after this many minutes",
    )
    parser.add_argument(
        "--database-stale-after-minutes",
        type=float,
        default=float(control_config.get("database_stale_after_minutes", "95")),
        help="consider started database rows stale after this many minutes",
    )
    parser.add_argument(
        "--recovery-max-runtime-minutes",
        type=float,
        default=float(control_config.get("recovery_max_runtime_minutes", "90")),
        help="cap recovered database runtimes at this many minutes",
    )
    parser.add_argument(
        "--repair-timestamps",
        action="store_true",
        help=(
            "repair excessive failed-job end times without running "
            "recovery layers"
        ),
    )
    parser.add_argument(
        "--recovery-pending-timeout-minutes",
        type=float,
        default=float(control_config.get("recovery_pending_timeout_minutes", "360")),
        help="cap recovered pending database lifetimes at this many minutes",
    )
    parser.add_argument(
        "--database-status-window-hours",
        type=float,
        default=float(control_config.get("database_status_window_hours", "24")),
        help="summarize database jobs started within this many hours",
    )
    parser.add_argument(
        "--database-accepted-stale-after-hours",
        type=float,
        default=float(
            control_config.get("database_accepted_stale_after_hours", "24")
        ),
        help="consider accepted database rows stale after this many hours",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="process at most this many stalled jobs in each selected layer",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=optional_path(config.get("server", "outputpath", fallback=None)),
        help="override the configured status-document directory",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(
            control_config.get("lock_file", "/run/lock/pywps-job-control.lock")
        ),
        help="override the configured process lock file",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="override the derived monitor log file",
    )
    parser.add_argument(
        "--event-log",
        type=Path,
        default=optional_path(control_config.get("event_log")) or job_event_log_file(config),
        help="append important operational events as JSON Lines",
    )
    parser.add_argument(
        "--access-log",
        type=Path,
        default=optional_path(control_config.get("access_log")),
        help="Nginx access log used to discover missing polled status documents",
    )
    parser.add_argument(
        "--poll-window-minutes",
        type=float,
        default=float(control_config.get("poll_window_minutes", "190")),
        help="inspect polling requests from this recent time window",
    )
    parser.add_argument(
        "--min-poll-count",
        type=int,
        default=int(control_config.get("min_poll_count", "3")),
        help="require this many 404 poll responses before recovery",
    )
    parser.add_argument(
        "--min-poll-duration-minutes",
        type=float,
        default=float(
            control_config.get(
                "min_poll_duration_minutes",
                "100",
            )
        ),
        help="require matching polls to span at least this much time",
    )
    parser.add_argument(
        "--no-database-guard",
        action="store_false",
        dest="database_guard",
        default=control_config.getboolean("missing_status_database_guard", fallback=True),
        help="allow polling recovery without checking active database requests",
    )
    args = parser.parse_args(argv)
    limit = args.limit
    recovery_limit = int(control_config.get("recovery_limit", "100"))
    missing_status_recovery_limit = int(
        control_config.get("missing_status_recovery_limit", "20")
    )
    incident_archive_enabled = control_config.getboolean(
        "incident_archive_enabled", fallback=False
    )
    incident_archive_dir = optional_path(control_config.get("incident_archive_dir"))
    layers = args.layer or DEFAULT_LAYERS[args.mode]
    invalid = sorted(set(layers) - set(SUPPORTED_LAYERS))
    if invalid:
        parser.error(f"unsupported layers: {', '.join(invalid)}")
    if not layers:
        parser.error("at least one layer must be configured")
    if args.stale_after_minutes <= 0:
        parser.error("--stale-after-minutes must be greater than zero")
    if args.database_stale_after_minutes <= 0:
        parser.error("--database-stale-after-minutes must be greater than zero")
    if args.recovery_max_runtime_minutes <= 0:
        parser.error("--recovery-max-runtime-minutes must be greater than zero")
    if args.recovery_max_runtime_minutes > args.database_stale_after_minutes:
        parser.error(
            "--recovery-max-runtime-minutes must not exceed "
            "--database-stale-after-minutes"
        )
    if args.recovery_pending_timeout_minutes <= 0:
        parser.error("--recovery-pending-timeout-minutes must be greater than zero")
    if (
        args.recovery_pending_timeout_minutes
        > args.database_accepted_stale_after_hours * 60
    ):
        parser.error(
            "--recovery-pending-timeout-minutes must not exceed "
            "--database-accepted-stale-after-hours"
        )
    if args.repair_timestamps and args.mode != "recover":
        parser.error("--repair-timestamps requires recover mode")
    if args.database_accepted_stale_after_hours <= 0:
        parser.error(
            "--database-accepted-stale-after-hours must be greater than zero"
        )
    if (
        args.database_accepted_stale_after_hours * 60
        <= args.database_stale_after_minutes
    ):
        parser.error(
            "--database-accepted-stale-after-hours must be longer than "
            "--database-stale-after-minutes"
        )
    if not 3 <= args.database_status_window_hours <= 24:
        parser.error("--database-status-window-hours must be between 3 and 24")
    if args.long_running_minutes <= 0:
        parser.error("--long-running-minutes must be greater than zero")
    if args.long_running_minutes >= args.stale_after_minutes:
        parser.error("--long-running-minutes must be shorter than --stale-after-minutes")
    if args.long_running_minutes >= args.database_stale_after_minutes:
        parser.error(
            "--long-running-minutes must be shorter than "
            "--database-stale-after-minutes"
        )
    if limit is not None and limit <= 0:
        parser.error("--limit must be greater than zero")
    if recovery_limit <= 0 or missing_status_recovery_limit <= 0:
        parser.error("configured recovery limits must be greater than zero")
    if incident_archive_enabled and incident_archive_dir is None:
        parser.error("incident_archive_dir is required when incident archiving is enabled")
    if args.poll_window_minutes <= 0:
        parser.error("--poll-window-minutes must be greater than zero")
    if args.min_poll_count <= 0:
        parser.error("--min-poll-count must be greater than zero")
    if args.min_poll_duration_minutes < 0:
        parser.error("--min-poll-duration-minutes cannot be negative")
    if args.min_poll_duration_minutes >= args.poll_window_minutes:
        parser.error("--min-poll-duration-minutes must be shorter than the polling window")
    return Settings(
        mode=args.mode,
        layers=list(dict.fromkeys(layers)),
        stale_after_minutes=args.stale_after_minutes,
        output_dir=args.output_dir,
        pywps_config=preliminary.config,
        lock_file=args.lock_file,
        log_file=args.log_file or job_monitor_log_file(config),
        event_log=args.event_log,
        show_summaries=args.show_summaries,
        human_readable=args.human_readable,
        limit=limit,
        output_url=config.get("server", "outputurl", fallback=None),
        access_log=args.access_log,
        poll_window_minutes=args.poll_window_minutes,
        min_poll_count=args.min_poll_count,
        min_poll_duration_minutes=args.min_poll_duration_minutes,
        database_guard=args.database_guard,
        repair_recovery_timestamps=args.repair_timestamps,
        monitor_enabled=control_config.getboolean("monitor_enabled", fallback=True),
        recovery_enabled=control_config.getboolean("recovery_enabled", fallback=False),
        missing_status_recovery_enabled=control_config.getboolean(
            "missing_status_recovery_enabled", fallback=False
        ),
        service_name=(preliminary.config.stem if preliminary.config else "unknown"),
        incident_archive_enabled=incident_archive_enabled,
        incident_archive_dir=incident_archive_dir,
        recovery_limit=recovery_limit,
        missing_status_recovery_limit=missing_status_recovery_limit,
        long_running_minutes=args.long_running_minutes,
        database_stale_after_minutes=args.database_stale_after_minutes,
        recovery_max_runtime_minutes=args.recovery_max_runtime_minutes,
        recovery_pending_timeout_minutes=args.recovery_pending_timeout_minutes,
        database_accepted_stale_after_hours=(
            args.database_accepted_stale_after_hours
        ),
        database_status_window_hours=args.database_status_window_hours,
        work_dir=optional_path(config.get("server", "workdir", fallback=None)),
        recovery_user=control_config.get("recovery_user", "wps"),
        recovery_group=control_config.get("recovery_group", "wps"),
        python_executable=optional_path(control_config.get("python_executable")),
        recovery_working_dir=optional_path(
            control_config.get("recovery_working_dir")
        ),
    )


def configure_logging(
    log_file: Path | None,
    show_summaries: bool = False,
    service_name: str = "unknown",
    event_log: Path | None = None,
) -> logging.Logger:
    stream_handler = logging.StreamHandler()
    service_filter = ServiceContextFilter(service_name)
    stream_handler.addFilter(service_filter)
    # Cron mails every byte written to stdout or stderr. Scheduled invocations
    # therefore expose only critical incidents; operator helpers explicitly
    # request summaries and retain their existing interactive output.
    stream_handler.setLevel(logging.INFO if show_summaries else logging.CRITICAL)
    if show_summaries:
        stream_handler.addFilter(SummaryConsoleFilter())
    handlers: list[logging.Handler] = [stream_handler]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.addFilter(service_filter)
        file_handler.setLevel(logging.INFO)
        handlers.append(file_handler)
    if event_log is not None:
        handlers.append(
            JsonlEventHandler(event_log, service_name, "pywps-job-control")
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s service=%(service)s %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("pywps-job-control")


def operation_is_enabled(settings: Settings) -> bool:
    if settings.mode == "monitor":
        return settings.monitor_enabled
    return settings.recovery_enabled


def layer_is_enabled(settings: Settings, layer: str) -> bool:
    if settings.mode == "recover" and layer == "polling":
        return settings.missing_status_recovery_enabled
    return True


def execute_layers(
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
    runners: dict[str, Callable[[Settings, datetime, logging.Logger], LayerSummary]] | None = None,
) -> list[LayerSummary]:
    runners = runners or {
        "xml": run_xml_layer,
        "database": run_database_layer,
        "polling": run_polling_layer,
    }
    summaries: list[LayerSummary] = []
    database_recovery_excluded_jobs: set[str] = set()
    if settings.mode == "recover" and settings.repair_recovery_timestamps:
        repair_limit = (
            settings.limit if settings.limit is not None else settings.recovery_limit
        )
        repair_settings = replace(settings, limit=repair_limit)
        try:
            repair_summary = repair_database_recovery_timestamps(
                repair_settings,
                now,
                logger,
            )
        except Exception as error:
            logger.critical(
                "layer=timestamps decision=error reason=%s", error, exc_info=True
            )
            repair_summary = LayerSummary("timestamps", errors=1)
        summaries.append(repair_summary)
        log_method = (
            logger.error
            if repair_summary.errors
            else logger.warning
            if repair_summary.repaired
            else logger.info
        )
        log_method(
            "summary layer=timestamps checked=%d repaired=%d errors=%d limit=%s",
            repair_summary.checked,
            repair_summary.repaired,
            repair_summary.errors,
            repair_limit,
        )
        return summaries
    layers = settings.layers
    if settings.mode == "recover":
        order = {"xml": 0, "database": 1, "polling": 2}
        layers = sorted(layers, key=order.__getitem__)
    for layer in layers:
        if not layer_is_enabled(settings, layer):
            logger.info(
                "layer=%s result=skip reason=recovery-disabled",
                layer,
            )
            continue
        try:
            layer_limit = settings.limit
            if settings.mode == "recover" and layer_limit is None:
                layer_limit = (
                    settings.missing_status_recovery_limit
                    if layer == "polling"
                    else settings.recovery_limit
                )
            layer_settings = replace(
                settings,
                limit=layer_limit,
                database_recovery_excluded_jobs=set(database_recovery_excluded_jobs),
            )
            summary = runners[layer](layer_settings, now, logger)
        except Exception as error:
            logger.critical(
                "layer=%s decision=error reason=%s", layer, error, exc_info=True
            )
            summary = LayerSummary(layer, errors=1)
        summaries.append(summary)
        database_recovery_excluded_jobs.update(summary.recovery_blocked_jobs)
        if summary.errors:
            log_summary = logger.error
        elif summary.stalled or summary.long_running:
            log_summary = logger.warning
        else:
            log_summary = logger.info
        if summary.name == "database" and summary.status_counts is not None:
            counts = summary.status_counts
            log_summary(
                "summary layer=database total=%d running=%d accepted=%d "
                "failed=%d success=%d stalled=%d long_running=%d recovered=%d "
                "errors=%d mode=%s limit=%s",
                summary.checked,
                counts["running"],
                counts["accepted"],
                counts["failed"],
                counts["successful"],
                summary.stalled,
                summary.long_running,
                summary.recovered,
                summary.errors,
                settings.mode,
                settings.limit if settings.limit is not None else "none",
            )
        else:
            log_summary(
                "summary layer=%s checked=%d stalled=%d long_running=%d "
                "recovered=%d errors=%d mode=%s limit=%s",
                summary.name,
                summary.checked,
                summary.stalled,
                summary.long_running,
                summary.recovered,
                summary.errors,
                settings.mode,
                settings.limit if settings.limit is not None else "none",
            )
    if settings.mode == "recover":
        recovered_jobs = {
            job_uuid for summary in summaries for job_uuid in summary.recovered_jobs
        }
        repair_jobs = recovered_jobs
        if repair_jobs:
            repair_limit = (
                settings.limit
                if settings.limit is not None
                else settings.recovery_limit
            )
            repair_settings = replace(settings, limit=repair_limit)
            try:
                repair_summary = repair_database_recovery_timestamps(
                    repair_settings,
                    now,
                    logger,
                    repair_jobs,
                )
            except Exception as error:
                logger.critical(
                    "layer=timestamps decision=error reason=%s", error, exc_info=True
                )
                repair_summary = LayerSummary("timestamps", errors=1)
            summaries.append(repair_summary)
            log_method = (
                logger.error
                if repair_summary.errors
                else logger.warning
                if repair_summary.repaired
                else logger.info
            )
            log_method(
                "summary layer=timestamps checked=%d repaired=%d errors=%d limit=%s",
                repair_summary.checked,
                repair_summary.repaired,
                repair_summary.errors,
                repair_limit,
            )
    return summaries


def operator_title(settings: Settings) -> str:
    if settings.mode == "recover" and settings.repair_recovery_timestamps:
        return f"PyWPS timestamp repair — {settings.service_name}"
    titles = {
        "monitor": "PyWPS monitor",
        "recover": "PyWPS recovery",
    }
    return f"{titles[settings.mode]} — {settings.service_name}"


def print_operator_report(settings: Settings, summaries: list[LayerSummary]) -> None:
    print(operator_title(settings))
    for summary in summaries:
        if summary.name == "timestamps":
            print(
                "Timestamps: "
                f"checked={summary.checked}  repaired={summary.repaired}  "
                f"errors={summary.errors}"
            )
            continue
        layer_label = "XML" if summary.name == "xml" else summary.name.capitalize()
        fields = [f"checked={summary.checked}", f"stalled={summary.stalled}"]
        if settings.mode != "recover":
            fields.append(f"long-running={summary.long_running}")
        if settings.mode == "recover":
            fields.append(f"recovered={summary.recovered}")
        fields.append(f"errors={summary.errors}")
        print(f"{layer_label}: " + "  ".join(fields))

    errors = sum(summary.errors for summary in summaries)
    stalled = sum(summary.stalled for summary in summaries)
    long_running = sum(summary.long_running for summary in summaries)
    recovered = sum(summary.recovered for summary in summaries)
    repaired = sum(summary.repaired for summary in summaries)
    if errors:
        result = f"completed with {errors} error{'s' if errors != 1 else ''}"
    elif settings.mode == "recover":
        actions = []
        if recovered:
            actions.append(f"recovered {recovered} job{'s' if recovered != 1 else ''}")
        if repaired:
            actions.append(
                f"repaired {repaired} timestamp{'s' if repaired != 1 else ''}"
            )
        result = ", ".join(actions) if actions else "no recovery needed"
    elif settings.mode == "monitor" and (stalled or long_running):
        result = "attention required"
    else:
        result = "healthy" if settings.mode == "monitor" else "complete"
    print(f"Result: {result}")
    if settings.log_file is not None:
        print(f"Details: {settings.log_file}")


def recover_job_dump_cli(arguments: list[str]) -> int:
    if len(arguments) != 5:
        raise ValueError("invalid internal dump-recovery invocation")
    config_path, dump_name, expected_uuid, status_name, message = arguments
    if UUID_RE.fullmatch(expected_uuid) is None:
        raise ValueError("invalid dump-recovery UUID")
    unresolved_dump_path = Path(dump_name)
    if unresolved_dump_path.is_symlink():
        raise ValueError("invalid dump-recovery job file")
    dump_path = unresolved_dump_path.resolve()
    status_path = Path(status_name).resolve()
    if not dump_path.is_file():
        raise ValueError("invalid dump-recovery job file")
    config = read_config(Path(config_path))
    validate_python_runtime(
        optional_path(config.get(JOB_CONTROL_SECTION, "python_executable", fallback=None))
    )
    os.environ["PYWPS_CFG"] = config_path
    from pywps import configuration
    from pywps.processing.job import Job
    from pywps.response.status import WPS_STATUS

    configuration.load_configuration([config_path])
    job = Job.load(str(dump_path))
    if str(job.uuid) != expected_uuid:
        raise ValueError("loaded PyWPS job UUID does not match")
    if Path(job.workdir).resolve() != dump_path.parent:
        raise ValueError("loaded PyWPS workdir does not match dump location")
    rendered_status = Path(job.wps_response.process.status_location).resolve()
    if rendered_status != status_path:
        raise ValueError("loaded PyWPS status destination does not match")
    job.wps_response._update_status(WPS_STATUS.FAILED, message, 100, clean=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--recover-job-dump"]:
        try:
            return recover_job_dump_cli(arguments[1:])
        except Exception as error:
            print(error, file=sys.stderr)
            return 2
    try:
        settings = parse_args(arguments)
        validate_python_runtime(settings.python_executable)
        logger = configure_logging(
            settings.log_file,
            settings.show_summaries,
            service_name=settings.service_name,
            event_log=settings.event_log,
        )
        if not operation_is_enabled(settings):
            logger.info(
                "decision=skip reason=operation-disabled mode=%s layers=%s",
                settings.mode,
                ",".join(settings.layers),
            )
            if settings.human_readable:
                print(f"{operator_title(settings)}\nResult: disabled")
            return 0
        settings.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with settings.lock_file.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.info("decision=skip reason=another-run-is-active")
                if settings.human_readable:
                    print(f"{operator_title(settings)}\nResult: another run is active")
                return 0
            lock.write(f"{os.getpid()}\n")
            lock.flush()
            summaries = execute_layers(settings, datetime.now(UTC), logger)
            if settings.human_readable:
                print_operator_report(settings, summaries)
            return 1 if any(summary.errors for summary in summaries) else 0
    except (OSError, ValueError) as error:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s service=unknown %(message)s",
        )
        logging.getLogger("pywps-job-control").critical("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
