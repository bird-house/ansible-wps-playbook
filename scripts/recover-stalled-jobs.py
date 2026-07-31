#!/usr/bin/env python3
"""Monitor or clean stalled PyWPS jobs in independent storage layers."""

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


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
FINAL_XML_STATES = {"ProcessSucceeded", "ProcessFailed"}
SUPPORTED_LAYERS = ("xml", "database")
LAYER_CHOICES = (*SUPPORTED_LAYERS, "all")
UTC = timezone.utc


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


@dataclass
class LayerSummary:
    name: str
    checked: int = 0
    stalled: int = 0
    cleaned: int = 0
    errors: int = 0


class SummaryConsoleFilter(logging.Filter):
    """Keep console output compact while allowing informational summaries."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or record.getMessage().startswith(
            "summary "
        )


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
        raise RuntimeError("status file changed before cleanup")
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
                logger.debug("layer=xml job=%s state=%s decision=final", document.job_uuid, document.state)
                continue
            if not is_stalled(document.last_update, now, threshold):
                logger.debug(
                    "layer=xml job=%s state=%s decision=recent creation=%s mtime=%s",
                    document.job_uuid,
                    document.state,
                    document.creation_time.isoformat(),
                    document.modification_time.isoformat(),
                )
                continue
            summary.stalled += 1
            logger.info(
                "layer=xml job=%s state=%s decision=stalled last_update=%s",
                document.job_uuid,
                document.state,
                document.last_update.isoformat(),
            )
            if settings.mode == "cleanup":
                message = (
                    "Process failed: stalled-job cleanup found no status update "
                    f"for at least {settings.stale_after_hours:g} hours."
                )
                write_failed_xml(document, tree, message, now)
                summary.cleaned += 1
                logger.warning("layer=xml job=%s decision=cleaned", document.job_uuid)
            if settings.limit is not None and summary.stalled >= settings.limit:
                break
        except Exception as error:
            summary.errors += 1
            logger.exception("layer=xml status=%s decision=error reason=%s", path, error)
    return summary


def database_last_update(record: object) -> datetime:
    value = getattr(record, "time_end", None) or getattr(record, "time_start", None)
    if value is None:
        raise ValueError("database record has no start or update time")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
        query = session.query(dblog.ProcessInstance).filter(
            or_(
                dblog.ProcessInstance.status.is_(None),
                dblog.ProcessInstance.status.notin_(
                    [WPS_STATUS.SUCCEEDED, WPS_STATUS.FAILED]
                ),
            )
        )
        if settings.limit is not None:
            cutoff = (now - threshold).astimezone(UTC).replace(tzinfo=None)
            last_update = func.coalesce(
                dblog.ProcessInstance.time_end,
                dblog.ProcessInstance.time_start,
            )
            query = (
                query.filter(last_update <= cutoff)
                .order_by(last_update, dblog.ProcessInstance.uuid)
                .limit(settings.limit)
            )
        records = query.all()
        for record in records:
            summary.checked += 1
            try:
                last_update = database_last_update(record)
                if not is_stalled(last_update, now, threshold):
                    logger.debug(
                        "layer=database job=%s status=%s decision=recent last_update=%s",
                        record.uuid,
                        record.status,
                        last_update.isoformat(),
                    )
                    continue
                summary.stalled += 1
                logger.info(
                    "layer=database job=%s status=%s decision=stalled last_update=%s",
                    record.uuid,
                    record.status,
                    last_update.isoformat(),
                )
                if settings.mode == "cleanup":
                    record.status = WPS_STATUS.FAILED
                    record.percent_done = 100
                    record.message = (
                        "Process failed: stalled-job cleanup found no database update "
                        f"for at least {settings.stale_after_hours:g} hours."
                    )
                    record.time_end = now.astimezone(UTC).replace(tzinfo=None)
                    session.query(dblog.RequestInstance).filter_by(uuid=record.uuid).delete()
                    session.commit()
                    summary.cleaned += 1
                    logger.warning("layer=database job=%s decision=cleaned", record.uuid)
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
    if not parser.has_section("stalled_jobs"):
        raise ValueError(f"missing [stalled_jobs] section in {path}")
    return parser


def optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def stalled_jobs_log_file(config: configparser.ConfigParser) -> Path | None:
    pywps_log_file = optional_path(config.get("logging", "file", fallback=None))
    if pywps_log_file is None:
        return None
    return pywps_log_file.with_name(f"stalled-jobs-{pywps_log_file.name}")


def parse_args(argv: list[str] | None = None) -> Settings:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path)
    preliminary, _ = pre_parser.parse_known_args(argv)
    config = read_config(preliminary.config)
    stalled_config = config["stalled_jobs"] if config.has_section("stalled_jobs") else {}

    configured_layers = [
        layer.strip()
        for layer in stalled_config.get("layers", "xml,database").split(",")
        if layer.strip()
    ]
    parser = argparse.ArgumentParser(description=__doc__, parents=[pre_parser])
    parser.add_argument("mode", choices=("monitor", "cleanup"))
    parser.add_argument(
        "--layer",
        action="append",
        choices=LAYER_CHOICES,
        help="run only this layer; repeat to select more than one; all selects both",
    )
    parser.add_argument(
        "--show-summaries",
        action="store_true",
        help="write every layer summary to the console",
    )
    parser.add_argument(
        "--stale-after-hours",
        "--hours",
        type=float,
        default=float(stalled_config.get("stale_after_hours", "6")),
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
    )
    parser.add_argument(
        "--pywps-config",
        type=Path,
        default=preliminary.config,
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(
            stalled_config.get("lock_file", "/run/lock/pywps-stalled-jobs.lock")
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=stalled_jobs_log_file(config),
    )
    args = parser.parse_args(argv)
    limit = args.limit
    if limit is None and args.mode == "cleanup":
        limit = int(stalled_config.get("cleanup_limit", "100"))
    layers = args.layer or configured_layers
    invalid = sorted(set(layers) - set(LAYER_CHOICES))
    if invalid:
        parser.error(f"unsupported configured layers: {', '.join(invalid)}")
    if not layers:
        parser.error("at least one layer must be configured")
    if "all" in layers:
        layers = list(SUPPORTED_LAYERS)
    if args.stale_after_hours <= 0:
        parser.error("--stale-after-hours/--hours must be greater than zero")
    if limit is not None and limit <= 0:
        parser.error("--limit must be greater than zero")
    return Settings(
        mode=args.mode,
        layers=list(dict.fromkeys(layers)),
        stale_after_hours=args.stale_after_hours,
        output_dir=args.output_dir,
        pywps_config=args.pywps_config,
        lock_file=args.lock_file,
        log_file=args.log_file,
        show_summaries=args.show_summaries,
        limit=limit,
    )


def configure_logging(
    log_file: Path | None, show_summaries: bool = False
) -> logging.Logger:
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO if show_summaries else logging.WARNING)
    if show_summaries:
        stream_handler.addFilter(SummaryConsoleFilter())
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
    return logging.getLogger("pywps-stalled-jobs")


def execute_layers(
    settings: Settings,
    now: datetime,
    logger: logging.Logger,
    runners: dict[str, Callable[[Settings, datetime, logging.Logger], LayerSummary]] | None = None,
) -> list[LayerSummary]:
    runners = runners or {"xml": run_xml_layer, "database": run_database_layer}
    summaries: list[LayerSummary] = []
    for layer in settings.layers:
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
            "summary layer=%s checked=%d stalled=%d cleaned=%d errors=%d "
            "mode=%s limit=%s",
            summary.name,
            summary.checked,
            summary.stalled,
            summary.cleaned,
            summary.errors,
            settings.mode,
            settings.limit if settings.limit is not None else "none",
        )
    return summaries


def main(argv: list[str] | None = None) -> int:
    try:
        settings = parse_args(argv)
        logger = configure_logging(settings.log_file, settings.show_summaries)
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
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("pywps-stalled-jobs").error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
