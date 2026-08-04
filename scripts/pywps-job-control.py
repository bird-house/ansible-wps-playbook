#!/usr/bin/env python3
"""Control PyWPS monitoring, recovery, and statistics across storage layers."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import io
import logging
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote, urlsplit


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
FINAL_XML_STATES = {"ProcessSucceeded", "ProcessFailed"}
XML_JOB_STATUSES = {
    "ProcessAccepted": "accepted",
    "ProcessStarted": "running",
    "ProcessPaused": "running",
    "ProcessSucceeded": "successful",
    "ProcessFailed": "failed",
}
SUPPORTED_LAYERS = ("xml", "database", "polling")
UTC = timezone.utc
JOB_CONTROL_SECTION = "job_control"
LEGACY_JOB_CONTROL_SECTION = "stalled_jobs"
ACCESS_LOG_RE = re.compile(
    r'^(?P<client>\S+) \S+ \S+ \[(?P<timestamp>[^]]+)\] '
    r'"(?P<method>\S+) (?P<target>\S+) (?P<protocol>[^"]+)" '
    r'(?P<status>\d{3})(?:\s|$)'
)
ACCESS_LOG_OLD_LINE_STOP_COUNT = 100


@dataclass
class Settings:
    mode: str
    layers: list[str]
    stale_after_hours: float
    output_dir: Path | None
    pywps_config: Path | None
    lock_file: Path
    log_file: Path | None
    show_summaries: bool = False
    limit: int | None = None
    status_counts: bool = False
    output_url: str | None = None
    access_log: Path | None = None
    poll_window_minutes: float = 60
    min_poll_count: int = 3
    min_poll_duration_minutes: float = 15
    database_guard: bool = True
    monitor_enabled: bool = True
    recovery_enabled: bool = False
    missing_status_recovery_enabled: bool = False
    statistics_enabled: bool = True
    service_name: str = "unknown"


@dataclass
class LayerSummary:
    name: str
    checked: int = 0
    stalled: int = 0
    recovered: int = 0
    errors: int = 0


class SummaryConsoleFilter(logging.Filter):
    """Keep console output compact while allowing informational summaries."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or record.getMessage().startswith(
            ("summary ", "status_summary ")
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
    namespaces: list[tuple[str, str]]

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


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


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

    namespaces: list[tuple[str, str]] = []
    for _, item in ET.iterparse(io.BytesIO(contents), events=("start-ns",)):
        if item not in namespaces:
            namespaces.append(item)
    tree = ET.ElementTree(ET.fromstring(contents))
    root = tree.getroot()
    status = next((element for element in root.iter() if local_name(element.tag) == "Status"), None)
    if status is None:
        raise ValueError("missing Status element")
    creation_value = status.attrib.get("creationTime")
    if not creation_value:
        raise ValueError("missing Status creationTime")
    try:
        creation_time = parse_timestamp(creation_value)
    except ValueError as error:
        raise ValueError(f"invalid Status creationTime: {creation_value}") from error

    states = [child for child in list(status) if local_name(child.tag).startswith("Process")]
    if len(states) != 1:
        raise ValueError("Status must contain exactly one process state")

    return (
        XmlStatus(
            path=path,
            job_uuid=path.stem,
            state=local_name(states[0].tag),
            creation_time=creation_time,
            modification_time=datetime.fromtimestamp(path_stat.st_mtime, UTC),
            source_identity=stat_identity(path_stat),
            namespaces=namespaces,
        ),
        tree,
    )


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


def write_failed_xml(
    document: XmlStatus,
    tree: ET.ElementTree,
    message: str,
    now: datetime,
) -> None:
    for prefix, uri in document.namespaces:
        if not re.fullmatch(r"ns\d+", prefix):
            ET.register_namespace(prefix, uri)

    root = tree.getroot()
    status = next(element for element in root.iter() if local_name(element.tag) == "Status")
    states = [child for child in list(status) if local_name(child.tag).startswith("Process")]
    if len(states) != 1 or local_name(states[0].tag) in FINAL_XML_STATES:
        raise RuntimeError("status is no longer non-final")

    wps_uri = namespace(status.tag) or namespace(states[0].tag)
    ows_uri = next(
        (uri for prefix, uri in document.namespaces if prefix == "ows"),
        "http://www.opengis.net/ows/1.1",
    )
    status.set("creationTime", now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    for child in list(status):
        status.remove(child)
    failed = ET.SubElement(status, qualified("ProcessFailed", wps_uri))
    # Match the WPS 1.0.0 status documents emitted by PyWPS. OWSLib accepts
    # this mixed wps:ExceptionReport/ows:Exception structure, so recovery must
    # not try to normalize it to a different namespace layout.
    report = ET.SubElement(failed, qualified("ExceptionReport", wps_uri))
    exception = ET.SubElement(
        report,
        qualified("Exception", ows_uri),
        {"exceptionCode": "NoApplicableCode", "locator": "None"},
    )
    exception_text = ET.SubElement(exception, qualified("ExceptionText", ows_uri))
    exception_text.text = message

    source_stat = document.path.stat()
    if stat_identity(source_stat) != document.source_identity:
        raise RuntimeError("status file changed before recovery")
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
        os.chmod(temporary_name, source_stat.st_mode & 0o777)
        if os.geteuid() == 0:
            os.chown(temporary_name, source_stat.st_uid, source_stat.st_gid)
        if stat_identity(document.path.stat()) != document.source_identity:
            raise RuntimeError("status file changed before atomic replacement")
        os.replace(temporary_name, document.path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def run_xml_layer(
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
) -> LayerSummary:
    summary = LayerSummary("xml")
    if settings.output_dir is None:
        raise ValueError("the XML layer requires output_dir")
    threshold = timedelta(hours=settings.stale_after_hours)
    for path in find_status_files(settings.output_dir):
        summary.checked += 1
        try:
            document, tree = read_xml_status(path)
            if document.state in FINAL_XML_STATES:
                logger.debug(
                    "layer=xml job=%s status=%s finding=final",
                    document.job_uuid,
                    xml_job_status(document.state),
                )
                continue
            if not is_stalled(document.last_update, now, threshold):
                logger.debug(
                    "layer=xml job=%s status=%s finding=recent creation=%s mtime=%s",
                    document.job_uuid,
                    xml_job_status(document.state),
                    document.creation_time.isoformat(),
                    document.modification_time.isoformat(),
                )
                continue
            summary.stalled += 1
            logger.info(
                "layer=xml job=%s status=%s finding=stalled updated=%s",
                document.job_uuid,
                xml_job_status(document.state),
                document.last_update.isoformat(),
            )
            if settings.mode == "recover":
                message = (
                    "Process failed: stalled-job recovery found no status update "
                    f"for at least {settings.stale_after_hours:g} hours."
                )
                write_failed_xml(document, tree, message, now)
                summary.recovered += 1
                logger.warning(
                    "layer=xml job=%s status=failed action=recovered",
                    document.job_uuid,
                )
            if settings.limit is not None and summary.stalled >= settings.limit:
                break
        except Exception as error:
            summary.errors += 1
            logger.exception("layer=xml file=%s result=error reason=%s", path, error)
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
                    summary.recovered += 1
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
            logger.exception(
                "layer=polling job=%s decision=error reason=%s",
                candidate.job_uuid,
                error,
            )
        if settings.limit is not None and summary.stalled >= settings.limit:
            break
    return summary


def database_last_update(record: object) -> datetime:
    value = getattr(record, "time_end", None) or getattr(record, "time_start", None)
    if value is None:
        raise ValueError("database record has no start or update time")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
        from sqlalchemy import create_engine, func, inspect, or_
        from sqlalchemy.orm import sessionmaker
    except ImportError as error:
        raise RuntimeError(
            "the database layer must run with the service Conda environment"
        ) from error

    threshold = timedelta(hours=settings.stale_after_hours)
    database_url = configuration.get_config_value("logging", "database")
    engine = create_engine(database_url)
    if not inspect(engine).has_table(dblog.ProcessInstance.__tablename__):
        engine.dispose()
        raise RuntimeError(
            f"PyWPS request table does not exist: {dblog.ProcessInstance.__tablename__}"
        )
    session = sessionmaker(bind=engine)()
    try:
        if settings.status_counts:
            status_counts = summarize_database_statuses(
                session.query(
                    dblog.ProcessInstance.status,
                    func.count(),
                )
                .group_by(dblog.ProcessInstance.status)
                .all(),
                WPS_STATUS,
            )
            logger.info(
                "status_summary total=%d accepted=%d running=%d "
                "successful=%d failed=%d dismissed=%d unmapped=%d",
                status_counts["total"],
                status_counts["accepted"],
                status_counts["running"],
                status_counts["successful"],
                status_counts["failed"],
                status_counts["dismissed"],
                status_counts["unmapped"],
            )
        query = session.query(dblog.ProcessInstance).filter(
            or_(
                dblog.ProcessInstance.status.is_(None),
                dblog.ProcessInstance.status.notin_(
                    [WPS_STATUS.SUCCEEDED, WPS_STATUS.FAILED]
                ),
            )
        )
        cutoff = (now - threshold).astimezone(UTC).replace(tzinfo=None)
        last_update = func.coalesce(
            dblog.ProcessInstance.time_end,
            dblog.ProcessInstance.time_start,
        )
        query = query.filter(
            or_(last_update.is_(None), last_update <= cutoff)
        ).order_by(last_update, dblog.ProcessInstance.uuid)
        if settings.limit is not None:
            query = query.limit(settings.limit)
        records = query.all()
        for record in records:
            summary.checked += 1
            try:
                last_update = database_last_update(record)
                if not is_stalled(last_update, now, threshold):
                    logger.debug(
                        "layer=database job=%s status=%s finding=recent updated=%s",
                        record.uuid,
                        database_job_status(record.status, WPS_STATUS) or "unmapped",
                        last_update.isoformat(),
                    )
                    continue
                summary.stalled += 1
                logger.info(
                    "layer=database job=%s status=%s finding=stalled updated=%s",
                    record.uuid,
                    database_job_status(record.status, WPS_STATUS) or "unmapped",
                    last_update.isoformat(),
                )
                if settings.mode == "recover":
                    record.status = WPS_STATUS.FAILED
                    record.percent_done = 100
                    record.message = (
                        "Process failed: stalled-job recovery found no database update "
                        f"for at least {settings.stale_after_hours:g} hours."
                    )
                    record.time_end = now.astimezone(UTC).replace(tzinfo=None)
                    session.query(dblog.RequestInstance).filter_by(uuid=record.uuid).delete()
                    session.commit()
                    summary.recovered += 1
                    logger.warning(
                        "layer=database job=%s status=failed action=recovered",
                        record.uuid,
                    )
            except Exception as error:
                session.rollback()
                summary.errors += 1
                logger.exception(
                    "layer=database job=%s decision=error reason=%s",
                    getattr(record, "uuid", "unknown"),
                    error,
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
    if not any(
        parser.has_section(section)
        for section in (JOB_CONTROL_SECTION, LEGACY_JOB_CONTROL_SECTION)
    ):
        raise ValueError(f"missing [job_control] section in {path}")
    return parser


def job_control_config(
    config: configparser.ConfigParser,
) -> configparser.SectionProxy:
    """Return current job-control settings, accepting the legacy section."""
    if config.has_section(JOB_CONTROL_SECTION):
        return config[JOB_CONTROL_SECTION]
    if config.has_section(LEGACY_JOB_CONTROL_SECTION):
        return config[LEGACY_JOB_CONTROL_SECTION]
    config.add_section(JOB_CONTROL_SECTION)
    return config[JOB_CONTROL_SECTION]


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


def job_statistics_log_file(config: configparser.ConfigParser) -> Path | None:
    return related_log_file(config, "stats")


def parse_args(argv: list[str] | None = None) -> Settings:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--config",
        type=Path,
        help="service PyWPS configuration containing paths and defaults",
    )
    preliminary, _ = pre_parser.parse_known_args(argv)
    config = read_config(preliminary.config)
    control_config = job_control_config(config)

    configured_layers = [
        layer.strip()
        for layer in control_config.get("layers", "xml,database").split(",")
        if layer.strip()
    ]
    parser = argparse.ArgumentParser(
        description=__doc__,
        parents=[pre_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s --config /etc/pywps/rook.cfg monitor
  %(prog)s --config /etc/pywps/rook.cfg monitor --layer polling
  %(prog)s --config /etc/pywps/rook.cfg recover --layer xml
  %(prog)s --config /etc/pywps/rook.cfg statistics
""",
    )
    parser.add_argument(
        "mode",
        metavar="{monitor,recover,statistics}",
        choices=("monitor", "recover", "statistics"),
        help="monitor without changes, recover as failed, or report statistics",
    )
    parser.add_argument(
        "--layer",
        action="append",
        choices=SUPPORTED_LAYERS,
        metavar="{xml,database,polling}",
        help="select xml, database, or polling; repeat for multiple layers",
    )
    parser.add_argument(
        "--show-summaries",
        action="store_true",
        help="write every layer summary to the console",
    )
    parser.add_argument(
        "--status-counts",
        action="store_true",
        help="show complete database status counts; intended for manual monitoring",
    )
    parser.add_argument(
        "--stale-after-hours",
        type=float,
        default=float(control_config.get("stale_after_hours", "6")),
        help="consider nonfinal jobs stalled after this many hours",
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
        help="override the derived monitor or statistics log file",
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
        default=float(control_config.get("poll_window_minutes", "60")),
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
                "15",
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
    if args.mode == "statistics":
        args.status_counts = True
    limit = args.limit
    if limit is None and args.mode == "recover":
        limit = int(control_config.get("recovery_limit", "100"))
    layers = args.layer or configured_layers
    invalid = sorted(set(layers) - set(SUPPORTED_LAYERS))
    if invalid:
        parser.error(f"unsupported configured layers: {', '.join(invalid)}")
    if not layers:
        parser.error("at least one layer must be configured")
    if args.stale_after_hours <= 0:
        parser.error("--stale-after-hours must be greater than zero")
    if limit is not None and limit <= 0:
        parser.error("--limit must be greater than zero")
    if args.status_counts and args.mode not in {"monitor", "statistics"}:
        parser.error("--status-counts is only available in monitor or statistics mode")
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
        stale_after_hours=args.stale_after_hours,
        output_dir=args.output_dir,
        pywps_config=preliminary.config,
        lock_file=args.lock_file,
        log_file=(
            args.log_file
            or (
                job_statistics_log_file(config)
                if args.mode == "statistics"
                else job_monitor_log_file(config)
            )
        ),
        show_summaries=args.show_summaries,
        limit=limit,
        status_counts=args.status_counts,
        output_url=config.get("server", "outputurl", fallback=None),
        access_log=args.access_log,
        poll_window_minutes=args.poll_window_minutes,
        min_poll_count=args.min_poll_count,
        min_poll_duration_minutes=args.min_poll_duration_minutes,
        database_guard=args.database_guard,
        monitor_enabled=control_config.getboolean("monitor_enabled", fallback=True),
        recovery_enabled=control_config.getboolean("recovery_enabled", fallback=False),
        missing_status_recovery_enabled=control_config.getboolean(
            "missing_status_recovery_enabled", fallback=False
        ),
        statistics_enabled=control_config.getboolean("statistics_enabled", fallback=True),
        service_name=(preliminary.config.stem if preliminary.config else "unknown"),
    )


def configure_logging(
    log_file: Path | None,
    show_summaries: bool = False,
    statistics_only: bool = False,
    service_name: str = "unknown",
) -> logging.Logger:
    stream_handler = logging.StreamHandler()
    service_filter = ServiceContextFilter(service_name)
    stream_handler.addFilter(service_filter)
    stream_handler.setLevel(
        logging.INFO if show_summaries else logging.ERROR if statistics_only else logging.WARNING
    )
    if show_summaries:
        stream_handler.addFilter(SummaryConsoleFilter())
    handlers: list[logging.Handler] = [stream_handler]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.addFilter(service_filter)
        file_handler.setLevel(logging.INFO)
        if statistics_only:
            file_handler.addFilter(SummaryConsoleFilter())
        handlers.append(file_handler)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s service=%(service)s %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("pywps-job-control")


def operation_is_enabled(settings: Settings) -> bool:
    if settings.mode == "statistics":
        return settings.statistics_enabled
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
    for layer in settings.layers:
        if not layer_is_enabled(settings, layer):
            logger.info(
                "layer=%s result=skip reason=recovery-disabled",
                layer,
            )
            continue
        try:
            summary = runners[layer](settings, now, logger)
        except Exception as error:
            logger.exception("layer=%s decision=error reason=%s", layer, error)
            summary = LayerSummary(layer, errors=1)
        summaries.append(summary)
        if summary.errors:
            log_summary = logger.error
        elif summary.stalled:
            log_summary = logger.warning
        else:
            log_summary = logger.info
        log_summary(
            "summary layer=%s checked=%d stalled=%d recovered=%d errors=%d "
            "mode=%s limit=%s",
            summary.name,
            summary.checked,
            summary.stalled,
            summary.recovered,
            summary.errors,
            settings.mode,
            settings.limit if settings.limit is not None else "none",
        )
    return summaries


def main(argv: list[str] | None = None) -> int:
    try:
        settings = parse_args(argv)
        logger = configure_logging(
            settings.log_file,
            settings.show_summaries,
            statistics_only=settings.mode == "statistics",
            service_name=settings.service_name,
        )
        if not operation_is_enabled(settings):
            logger.info(
                "decision=skip reason=operation-disabled mode=%s layers=%s",
                settings.mode,
                ",".join(settings.layers),
            )
            return 0
        settings.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with settings.lock_file.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.info("decision=skip reason=another-run-is-active")
                return 0
            lock.write(f"{os.getpid()}\n")
            lock.flush()
            summaries = execute_layers(settings, datetime.now(UTC), logger)
            return 1 if any(summary.errors for summary in summaries) else 0
    except (OSError, ValueError) as error:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s service=unknown %(message)s",
        )
        logging.getLogger("pywps-job-control").error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
