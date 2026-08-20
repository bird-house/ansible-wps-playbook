#!/usr/bin/env python3
"""Shared JSON Lines event utilities for WPS operational tools."""

from __future__ import annotations

import fcntl
import gzip
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TextIO


UTC = timezone.utc
SCHEMA_VERSION = 1
FIELD_RE = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_-]*)=(\S+)")


def now_text() -> str:
    return datetime.now(UTC).isoformat()


def append_events(path: Path, records: Iterable[dict[str, object]]) -> None:
    """Append complete JSONL records under an advisory inter-process lock."""
    prepared = []
    for source in records:
        record = dict(source)
        record.setdefault("schema_version", SCHEMA_VERSION)
        record.setdefault("recorded_at", now_text())
        prepared.append(record)
    if not prepared:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            stream.writelines(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in prepared
            )
            stream.flush()
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def parsed_fields(message: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in FIELD_RE.findall(message):
        normalized: object = value
        if value in {"none", "unknown"}:
            normalized = None
        elif re.fullmatch(r"-?[0-9]+", value):
            normalized = int(value)
        elif re.fullmatch(r"-?[0-9]+\.[0-9]+", value):
            normalized = float(value)
        fields[key.replace("-", "_")] = normalized
    return fields


def event_name(level: str, fields: dict[str, object]) -> str:
    if fields.get("action") == "recovered":
        return "job-recovered"
    if fields.get("finding") == "long-running":
        return "job-long-running"
    if fields.get("finding"):
        return str(fields["finding"])
    if fields.get("decision") == "error" or level == "critical":
        return "operation-error"
    return "operation-warning"


class JsonlEventHandler(logging.Handler):
    """Persist recovery actions and warning-level operational events."""

    def __init__(self, path: Path, service: str, source: str) -> None:
        # Recovery actions are INFO; other INFO diagnostics stay in the normal
        # job-control log and do not inflate the durable event stream.
        super().__init__(level=logging.INFO)
        self.path = path
        self.service = service
        self.source = source

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            fields = parsed_fields(message)
            job_id = fields.get("job")
            name = event_name(record.levelname.lower(), fields)
            if record.levelno < logging.WARNING and name != "job-recovered":
                return
            event = {
                "record_type": "operation",
                "event": name,
                "service": self.service,
                "source": self.source,
                "level": record.levelname.lower(),
                "message": message,
                "fields": fields,
            }
            if isinstance(job_id, str):
                event["job_id"] = job_id
            append_events(self.path, [event])
        except Exception:
            self.handleError(record)


def open_jsonl(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8")
    return path.open(encoding="utf-8")
