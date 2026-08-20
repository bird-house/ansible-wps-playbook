#!/usr/bin/env python3
"""Record a compact, per-request summary from PyWPS status XML files."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from uuid import UUID

from wps_tools_events import append_events


UTC = timezone.utc
FINAL_STATES = {"ProcessSucceeded": "successful", "ProcessFailed": "failed"}
UUID_EPOCH_100NS = 0x01B21DD213814000
MAX_VALUE_LENGTH = 1000
MAX_DIAGNOSTIC_LENGTH = 8000


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def first_descendant(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    return next((item for item in element.iter() if local_name(item.tag) == name), None)


def element_text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    if not value:
        return None
    return value[:MAX_VALUE_LENGTH]


def uuid_time(value: str) -> datetime | None:
    try:
        identifier = UUID(value)
    except ValueError:
        return None
    if identifier.version != 1:
        return None
    seconds = (identifier.time - UUID_EPOCH_100NS) / 10_000_000
    return datetime.fromtimestamp(seconds, UTC)


def xml_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def input_value(input_element: ET.Element) -> dict[str, object]:
    reference = first_descendant(input_element, "Reference")
    if reference is not None:
        href = next(
            (value for key, value in reference.attrib.items() if local_name(key) == "href"),
            None,
        )
        return {"type": "reference", "value": unquote(href) if href else None}
    for kind in ("LiteralData", "ComplexData", "BoundingBoxData"):
        value = first_descendant(input_element, kind)
        if value is not None:
            result: dict[str, object] = {"type": kind, "value": element_text(value)}
            if value.attrib:
                result["attributes"] = {
                    local_name(key): item for key, item in sorted(value.attrib.items())
                }
            return result
    return {"type": "unknown", "value": None}


def inspect(
    path: Path, service: str, diagnostics: list[dict[str, str]] | None = None
) -> dict[str, object] | None:
    root = ET.parse(path).getroot()
    status = first_descendant(root, "Status")
    if status is None:
        raise ValueError("missing Status")
    states = [item for item in list(status) if local_name(item.tag) in FINAL_STATES]
    if not states:
        return None
    if len(states) != 1:
        raise ValueError("Status must contain exactly one final state")

    state = local_name(states[0].tag)
    job_id = path.stem
    finished = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    started = uuid_time(job_id)
    duration_source = "uuid1"
    if started is None:
        started = xml_time(status.get("creationTime"))
        duration_source = "status_creation_time"
    duration = max(0.0, (finished - started).total_seconds()) if started else None

    process = first_descendant(root, "Process")
    process_identifier = element_text(first_descendant(process, "Identifier"))
    inputs: dict[str, list[dict[str, object]]] = {}
    data_inputs = first_descendant(root, "DataInputs")
    if data_inputs is not None:
        for item in (child for child in list(data_inputs) if local_name(child.tag) == "Input"):
            name = element_text(first_descendant(item, "Identifier")) or "unknown"
            inputs.setdefault(name, []).append(input_value(item))

    failures = []
    if state == "ProcessFailed":
        for exception in (
            item for item in states[0].iter() if local_name(item.tag) == "Exception"
        ):
            failures.append(
                {
                    "code": exception.get("exceptionCode"),
                    "locator": exception.get("locator"),
                    "message": element_text(first_descendant(exception, "ExceptionText")),
                }
            )
        if not failures:
            failures.append({"code": None, "locator": None, "message": element_text(states[0])})

    return {
        "record_type": "request",
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "service": service,
        "job_id": job_id,
        "process": process_identifier or "unknown",
        "inputs": inputs,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat(),
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "duration_source": duration_source if duration is not None else None,
        "outcome": FINAL_STATES[state],
        "failures": failures,
        "diagnostics": (diagnostics or []) if state == "ProcessFailed" else [],
    }


def load_job_diagnostics(work_dir: Path | None) -> dict[str, list[dict[str, str]]]:
    if work_dir is None or not work_dir.is_dir():
        return {}
    result = {}
    for dump_path in work_dir.glob("pywps_process_*/job_*.dump"):
        try:
            with dump_path.open(encoding="utf-8") as stream:
                dump = json.load(stream)
            job_id = str(dump.get("process", {}).get("uuid") or "")
            if not job_id:
                continue
            error_path = dump_path.parent / "job-error.txt"
            with error_path.open("rb") as stream:
                stream.seek(max(0, error_path.stat().st_size - MAX_DIAGNOSTIC_LENGTH))
                message = " ".join(stream.read().decode("utf-8", errors="replace").split())
            if message:
                result[job_id] = [{"source": "job-error.txt", "message": message}]
        except (AttributeError, json.JSONDecodeError, OSError, TypeError):
            continue
    return result


def read_state(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("state file must contain a JSON string list")
    return set(value)


def write_state(path: Path, job_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(sorted(job_ids), stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="PyWPS configuration file")
    parser.add_argument("--output-dir", type=Path, help="status XML directory override")
    parser.add_argument("--service", help="service name (defaults to config filename)")
    parser.add_argument("--state-file", type=Path, help="only emit newly completed jobs")
    parser.add_argument("--log-file", type=Path, help="append JSON lines instead of stdout")
    parser.add_argument("--lock-file", type=Path, help="skip when another scan is active")
    args = parser.parse_args(argv)
    if args.config is None and args.output_dir is None:
        parser.error("one of --config or --output-dir is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with ExitStack() as resources:
        if args.lock_file is not None:
            args.lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock = resources.enter_context(args.lock_file.open("w", encoding="utf-8"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            lock.write(f"{os.getpid()}\n")
            lock.flush()

        config = configparser.ConfigParser()
        if args.config is not None:
            with args.config.open(encoding="utf-8") as stream:
                config.read_file(stream)
        output_dir = args.output_dir
        if output_dir is None:
            output_dir = Path(config.get("server", "outputpath"))
        service = args.service or (args.config.stem if args.config else output_dir.name)
        work_dir = None
        if args.config is not None:
            configured_work_dir = config.get("server", "workdir", fallback="").strip()
            work_dir = Path(configured_work_dir) if configured_work_dir else None
        job_diagnostics = load_job_diagnostics(work_dir)

        previous = read_state(args.state_file)
        current: set[str] = set()
        records = []
        errors = 0
        for path in sorted(output_dir.glob("*.xml")):
            try:
                record = inspect(path, service, job_diagnostics.get(path.stem))
            except (ET.ParseError, OSError, ValueError) as error:
                print(f"{path}: {error}", file=sys.stderr)
                errors += 1
                continue
            if record is None:
                continue
            job_id = str(record["job_id"])
            current.add(job_id)
            if args.state_file is None or job_id not in previous:
                records.append(record)

        if args.log_file:
            append_events(args.log_file, records)
        else:
            for record in records:
                print(json.dumps(record, sort_keys=True))
        if args.state_file is not None:
            write_state(args.state_file, current)
        return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
