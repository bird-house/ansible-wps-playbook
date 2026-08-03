from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "recover-stalled-jobs.py"
SPEC = importlib.util.spec_from_file_location("recover_stalled_jobs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

WPS = "http://www.opengis.net/wps/1.0.0"
OWS = "http://www.opengis.net/ows/1.1"
UTC = timezone.utc
JOB_UUID = "123e4567-e89b-42d3-a456-426614174000"
OTHER_JOB_UUID = "223e4567-e89b-42d3-a456-426614174000"
OWSLIB_AVAILABLE = importlib.util.find_spec("owslib") is not None


def status_xml(state: str, creation_time: datetime) -> str:
    timestamp = creation_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wps:ExecuteResponse xmlns:wps="{WPS}" xmlns:ows="{OWS}"
  service="WPS" version="1.0.0" serviceInstance="https://example.test/wps"
  statusLocation="https://example.test/outputs/{JOB_UUID}.xml">
  <wps:Process wps:processVersion="1.0.0">
    <ows:Identifier>test-process</ows:Identifier>
    <ows:Title>Test process</ows:Title>
    <ows:Abstract>Test process for stalled-job recovery</ows:Abstract>
  </wps:Process>
  <wps:Status creationTime="{timestamp}">
    <wps:{state} percentCompleted="10">Working</wps:{state}>
  </wps:Status>
</wps:ExecuteResponse>
"""


class RecoverStalledJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.outputs = self.root / "outputs"
        self.outputs.mkdir()
        self.status = self.outputs / f"{JOB_UUID}.xml"
        self.now = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)

    def tearDown(self):
        self.temporary.cleanup()

    def write_status(
        self,
        state: str = "ProcessStarted",
        creation_time: datetime | None = None,
        modification_time: datetime | None = None,
    ) -> None:
        creation_time = creation_time or self.now - timedelta(hours=8)
        modification_time = modification_time or self.now - timedelta(hours=8)
        self.status.write_text(status_xml(state, creation_time), encoding="utf-8")
        timestamp = modification_time.timestamp()
        os.utime(self.status, (timestamp, timestamp))

    def settings(self, mode: str = "monitor", layers=None):
        return MODULE.Settings(
            mode=mode,
            layers=layers or ["xml"],
            stale_after_hours=6,
            output_dir=self.outputs,
            pywps_config=None,
            lock_file=self.root / "lock",
            log_file=None,
        )

    def access_log_line(
        self,
        timestamp: datetime,
        status: int = 404,
        path: str | None = None,
        client: str = "192.0.2.10",
    ) -> str:
        path = path or f"/outputs/alpha/{JOB_UUID}.xml"
        nginx_time = timestamp.strftime("%d/%b/%Y:%H:%M:%S %z")
        return (
            f'{client} - - [{nginx_time}] "GET {path} HTTP/1.1" '
            f'{status} 153 "-" "python-requests/2.34.2" "-"\n'
        )

    def access_log_settings(self, mode: str = "cleanup"):
        settings = self.settings(mode, ["access-log"])
        settings.output_url = "https://example.test/outputs/alpha"
        settings.access_log = self.root / "access.log"
        settings.poll_window_minutes = 60
        settings.min_poll_count = 3
        settings.min_poll_duration_minutes = 5
        settings.database_guard = False
        return settings

    def test_xml_is_stalled_only_when_creation_time_and_mtime_are_old(self):
        self.write_status()
        document, _ = MODULE.read_xml_status(self.status)
        self.assertTrue(MODULE.is_stalled(document.last_update, self.now, timedelta(hours=6)))

        self.write_status(creation_time=self.now - timedelta(hours=1))
        document, _ = MODULE.read_xml_status(self.status)
        self.assertFalse(MODULE.is_stalled(document.last_update, self.now, timedelta(hours=6)))

        self.write_status(modification_time=self.now - timedelta(hours=1))
        document, _ = MODULE.read_xml_status(self.status)
        self.assertFalse(MODULE.is_stalled(document.last_update, self.now, timedelta(hours=6)))

    def test_xml_timestamps_are_normalized_to_utc(self):
        self.assertEqual(
            MODULE.parse_timestamp("2026-07-31T12:00:00+02:00"),
            self.now,
        )
        self.assertEqual(
            MODULE.parse_timestamp("2026-07-31T10:00:00Z"),
            self.now,
        )
        self.assertEqual(
            MODULE.parse_timestamp("2026-07-31T10:00:00"),
            self.now,
        )

    def test_monitor_reports_nonfinal_status_without_changing_it(self):
        self.write_status("ProcessAccepted")
        before = self.status.read_bytes()
        summary = MODULE.run_xml_layer(self.settings(), self.now, mock.Mock())
        self.assertEqual(
            (summary.checked, summary.stalled, summary.cleaned, summary.errors),
            (1, 1, 0, 0),
        )
        self.assertEqual(self.status.read_bytes(), before)

    def test_limit_caps_stalled_xml_jobs(self):
        self.write_status("ProcessAccepted")
        second_status = self.outputs / "223e4567-e89b-42d3-a456-426614174000.xml"
        second_status.write_text(
            status_xml("ProcessAccepted", self.now - timedelta(hours=8)),
            encoding="utf-8",
        )
        old = (self.now - timedelta(hours=8)).timestamp()
        os.utime(second_status, (old, old))

        settings = self.settings()
        settings.limit = 1
        summary = MODULE.run_xml_layer(settings, self.now, mock.Mock())
        self.assertEqual((summary.checked, summary.stalled), (1, 1))

    def test_cleanup_changes_any_old_nonfinal_status_to_failed(self):
        self.write_status("ProcessAccepted")
        summary = MODULE.run_xml_layer(self.settings("cleanup"), self.now, mock.Mock())
        self.assertEqual((summary.stalled, summary.cleaned, summary.errors), (1, 1, 0))

        root = ET.parse(self.status).getroot()
        status = root.find(f".//{{{WPS}}}Status")
        self.assertEqual(status.attrib["creationTime"], "2026-07-31T10:00:00Z")
        failed = status.find(f"{{{WPS}}}ProcessFailed")
        self.assertIsNotNone(failed)
        report = failed.find(f"{{{WPS}}}ExceptionReport")
        self.assertIsNotNone(report)
        exception = report.find(f"{{{OWS}}}Exception")
        self.assertEqual(exception.attrib["exceptionCode"], "NoApplicableCode")
        self.assertEqual(exception.attrib["locator"], "None")
        self.assertIn(
            "at least 6 hours",
            status.find(f".//{{{OWS}}}ExceptionText").text,
        )

    @unittest.skipUnless(
        OWSLIB_AVAILABLE,
        "OWSLib is not installed",
    )
    def test_rewritten_xml_round_trips_as_a_pywps_execute_response(self):
        from owslib.wps import WPSExecution

        self.write_status("ProcessStarted")
        summary = MODULE.run_xml_layer(self.settings("cleanup"), self.now, mock.Mock())
        self.assertEqual((summary.cleaned, summary.errors), (1, 0))

        document, _ = MODULE.read_xml_status(self.status)
        self.assertEqual(document.state, "ProcessFailed")
        self.assertEqual(document.creation_time, self.now)

        root = ET.parse(self.status).getroot()
        self.assertEqual(root.attrib["service"], "WPS")
        self.assertEqual(root.attrib["version"], "1.0.0")
        self.assertEqual(
            root.findtext(f".//{{{OWS}}}Identifier"),
            "test-process",
        )

        execution = WPSExecution()
        execution.checkStatus(response=self.status.read_bytes(), sleepSecs=0)
        # OWSLib reports an embedded ProcessFailed exception as "Exception".
        self.assertEqual(execution.status, "Exception")
        self.assertTrue(execution.isComplete())
        self.assertEqual(len(execution.errors), 1)
        self.assertEqual(execution.errors[0].code, "NoApplicableCode")
        self.assertEqual(execution.errors[0].locator, "None")
        self.assertIn("at least 6 hours", execution.errors[0].text)

    def test_final_statuses_are_never_stalled(self):
        for state in ("ProcessSucceeded", "ProcessFailed"):
            with self.subTest(state=state):
                self.write_status(state)
                before = self.status.read_bytes()
                summary = MODULE.run_xml_layer(self.settings("cleanup"), self.now, mock.Mock())
                self.assertEqual(summary.stalled, 0)
                self.assertEqual(self.status.read_bytes(), before)

    def test_unknown_process_state_is_nonfinal(self):
        self.write_status("ProcessQueued")
        summary = MODULE.run_xml_layer(self.settings(), self.now, mock.Mock())
        self.assertEqual(summary.stalled, 1)

    def test_invalid_creation_time_is_an_error_and_is_not_cleaned(self):
        self.status.write_text(status_xml("ProcessStarted", self.now).replace(
            "2026-07-31T10:00:00Z", "not-a-time"
        ), encoding="utf-8")
        before = self.status.read_bytes()
        summary = MODULE.run_xml_layer(self.settings("cleanup"), self.now, mock.Mock())
        self.assertEqual(summary.errors, 1)
        self.assertEqual(self.status.read_bytes(), before)

    def test_changed_status_is_not_replaced(self):
        self.write_status()
        document, tree = MODULE.read_xml_status(self.status)
        self.status.write_text(status_xml("ProcessStarted", self.now), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed before cleanup"):
            MODULE.write_failed_xml(document, tree, "stalled", self.now)

    def test_access_log_cleanup_creates_failure_after_repeated_recent_404s(self):
        settings = self.access_log_settings()
        settings.access_log.write_text(
            "".join(
                self.access_log_line(self.now - timedelta(minutes=minutes))
                for minutes in (12, 8, 2)
            ),
            encoding="utf-8",
        )

        logger = mock.Mock()
        summary = MODULE.run_access_log_layer(settings, self.now, logger)

        self.assertEqual(
            (summary.checked, summary.stalled, summary.cleaned, summary.errors),
            (3, 1, 1, 0),
        )
        document, _ = MODULE.read_xml_status(self.status)
        self.assertEqual(document.state, "ProcessFailed")
        self.assertEqual(document.creation_time, self.now)
        self.assertEqual(self.status.stat().st_mode & 0o777, 0o644)
        logger.warning.assert_called_once_with(
            "layer=access-log job=%s decision=created-failure-status polls=%d",
            JOB_UUID,
            3,
        )

    def test_access_log_monitor_reports_but_does_not_create_status(self):
        settings = self.access_log_settings("monitor")
        settings.access_log.write_text(
            "".join(
                self.access_log_line(self.now - timedelta(minutes=minutes))
                for minutes in (12, 8, 2)
            ),
            encoding="utf-8",
        )
        summary = MODULE.run_access_log_layer(settings, self.now, mock.Mock())
        self.assertEqual((summary.stalled, summary.cleaned), (1, 0))
        self.assertFalse(self.status.exists())

    def test_access_log_database_veto_protects_an_active_request(self):
        settings = self.access_log_settings()
        settings.database_guard = True
        settings.pywps_config = self.root / "pywps.cfg"
        settings.access_log.write_text(
            "".join(
                self.access_log_line(self.now - timedelta(minutes=minutes))
                for minutes in (12, 8, 2)
            ),
            encoding="utf-8",
        )
        with mock.patch.object(
            MODULE,
            "database_recovery_vetoes",
            return_value={JOB_UUID: "database-request-is-nonfinal"},
        ):
            logger = mock.Mock()
            summary = MODULE.run_access_log_layer(settings, self.now, logger)
        self.assertEqual((summary.stalled, summary.cleaned), (0, 0))
        self.assertFalse(self.status.exists())
        logger.info.assert_called_with(
            "layer=access-log job=%s decision=skip reason=%s polls=%d",
            JOB_UUID,
            "database-request-is-nonfinal",
            3,
        )

    def test_access_log_requires_exact_path_404_count_window_and_age(self):
        settings = self.access_log_settings()
        lines = [
            self.access_log_line(self.now - timedelta(minutes=20)),
            self.access_log_line(self.now - timedelta(minutes=10), status=200),
            self.access_log_line(
                self.now - timedelta(minutes=8),
                path=f"/other/alpha/{JOB_UUID}.xml",
            ),
            self.access_log_line(self.now - timedelta(minutes=70)),
            self.access_log_line(self.now - timedelta(minutes=2)),
        ]
        settings.access_log.write_text("".join(lines), encoding="utf-8")
        summary = MODULE.run_access_log_layer(settings, self.now, mock.Mock())
        self.assertEqual((summary.checked, summary.stalled, summary.cleaned), (0, 0, 0))
        self.assertFalse(self.status.exists())

    def test_shared_access_log_is_filtered_for_the_configured_service(self):
        settings = self.access_log_settings("monitor")
        alpha_lines = "".join(
            self.access_log_line(self.now - timedelta(minutes=minutes))
            for minutes in (20, 10, 2)
        )
        beta_lines = "".join(
            self.access_log_line(
                self.now - timedelta(minutes=minutes),
                path=f"/outputs/beta/{OTHER_JOB_UUID}.xml",
            )
            for minutes in (30, 15, 1)
        )
        settings.access_log.write_text(alpha_lines + beta_lines, encoding="utf-8")

        candidates = MODULE.find_missing_status_polls(settings, self.now)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].job_uuid, JOB_UUID)
        self.assertEqual(candidates[0].request_path, f"/outputs/alpha/{JOB_UUID}.xml")
        self.assertEqual(candidates[0].count, 3)

    def test_access_log_deduplicates_lines_copied_during_rotation(self):
        settings = self.access_log_settings()
        lines = "".join(
            self.access_log_line(
                self.now - timedelta(minutes=minutes),
                client=f"192.0.2.{index}",
            )
            for index, minutes in enumerate((12, 8, 2), start=10)
        )
        settings.access_log.write_text(lines, encoding="utf-8")
        settings.access_log.with_name("access.log.1").write_text(lines, encoding="utf-8")
        summary = MODULE.run_access_log_layer(settings, self.now, mock.Mock())
        self.assertEqual(summary.checked, 3)
        self.assertEqual(summary.cleaned, 1)

    def test_access_log_requires_polls_spanning_the_minimum_age(self):
        settings = self.access_log_settings()
        line = self.access_log_line(self.now - timedelta(minutes=10))
        settings.access_log.write_text(line * 3, encoding="utf-8")
        summary = MODULE.run_access_log_layer(settings, self.now, mock.Mock())
        self.assertEqual(summary.checked, 0)
        self.assertEqual(summary.cleaned, 0)

    def test_access_log_does_not_replace_status_created_concurrently(self):
        candidate = MODULE.MissingStatusPolls(
            JOB_UUID,
            f"/outputs/alpha/{JOB_UUID}.xml",
            self.now - timedelta(minutes=10),
            self.now - timedelta(minutes=1),
            3,
        )
        contents = MODULE.missing_status_xml(
            candidate, "https://example.test/outputs/alpha", self.now
        )
        self.status.write_bytes(b"created by pywps")
        self.assertFalse(MODULE.create_missing_status_file(self.status, contents))
        self.assertEqual(self.status.read_bytes(), b"created by pywps")

    @unittest.skipUnless(OWSLIB_AVAILABLE, "OWSLib is not installed")
    def test_missing_status_failure_stops_owslib_polling(self):
        from owslib.wps import WPSExecution

        candidate = MODULE.MissingStatusPolls(
            JOB_UUID,
            f"/outputs/alpha/{JOB_UUID}.xml",
            self.now - timedelta(minutes=10),
            self.now - timedelta(minutes=1),
            3,
        )
        self.status.write_bytes(
            MODULE.missing_status_xml(
                candidate, "https://example.test/outputs/alpha", self.now
            )
        )
        execution = WPSExecution()
        execution.checkStatus(response=self.status.read_bytes(), sleepSecs=0)
        self.assertEqual(execution.status, "Exception")
        self.assertTrue(execution.isComplete())
        self.assertIn("can no longer be monitored", execution.errors[0].text)

    def test_generated_configuration_supplies_defaults_and_cli_overrides(self):
        config = self.root / "stalled.ini"
        config.write_text(
            "[server]\n"
            f"outputpath = {self.outputs}\n"
            "outputurl = https://example.test/outputs/alpha\n"
            "[logging]\n"
            f"file = {self.root / 'logs' / 'alpha.log'}\n"
            "[stalled_jobs]\n"
            "layers = xml, database\n"
            "stale_after_hours = 6\n"
            "cleanup_limit = 100\n"
            f"access_log = {self.root / 'nginx-access.log'}\n"
            "poll_window_minutes = 45\n"
            "min_poll_count = 4\n"
            "min_poll_duration_minutes = 7\n"
            "missing_status_database_guard = true\n"
            f"lock_file = {self.root / 'configured.lock'}\n",
            encoding="utf-8",
        )
        settings = MODULE.parse_args(["--config", str(config), "monitor"])
        self.assertEqual(settings.layers, ["xml", "database"])
        self.assertEqual(settings.stale_after_hours, 6)
        self.assertIsNone(settings.limit)
        self.assertEqual(settings.output_dir, self.outputs)
        self.assertEqual(settings.pywps_config, config)
        self.assertEqual(settings.output_url, "https://example.test/outputs/alpha")
        self.assertEqual(settings.access_log, self.root / "nginx-access.log")
        self.assertEqual(settings.poll_window_minutes, 45)
        self.assertEqual(settings.min_poll_count, 4)
        self.assertEqual(settings.min_poll_duration_minutes, 7)
        self.assertTrue(settings.database_guard)
        self.assertEqual(
            settings.log_file,
            self.root / "logs" / "stalled-jobs-alpha.log",
        )

        overridden = MODULE.parse_args([
            "--config",
            str(config),
            "cleanup",
            "--layer",
            "xml",
            "--stale-after-hours",
            "12",
        ])
        self.assertEqual(overridden.layers, ["xml"])
        self.assertEqual(overridden.stale_after_hours, 12)
        self.assertEqual(overridden.limit, 100)

        all_layers = MODULE.parse_args([
            "--config",
            str(config),
            "monitor",
            "--layer",
            "all",
            "--show-summaries",
            "--status-counts",
        ])
        self.assertEqual(all_layers.layers, ["xml", "database"])
        self.assertTrue(all_layers.show_summaries)
        self.assertTrue(all_layers.status_counts)

        hours_alias = MODULE.parse_args([
            "--config",
            str(config),
            "monitor",
            "--hours",
            "3.5",
            "--limit",
            "500",
        ])
        self.assertEqual(hours_alias.stale_after_hours, 3.5)
        self.assertEqual(hours_alias.limit, 500)

    def test_layers_run_independently(self):
        def broken(*_args):
            raise RuntimeError("broken XML layer")

        def working(*_args):
            return MODULE.LayerSummary("database", checked=2, stalled=1)

        summaries = MODULE.execute_layers(
            self.settings(layers=["xml", "database"]),
            self.now,
            mock.Mock(),
            runners={"xml": broken, "database": working},
        )
        self.assertEqual(summaries[0].errors, 1)
        self.assertEqual((summaries[1].checked, summaries[1].stalled), (2, 1))

    def test_summary_severity_reflects_layer_result(self):
        cases = (
            (MODULE.LayerSummary("xml", checked=2), "info"),
            (MODULE.LayerSummary("xml", checked=2, stalled=1), "warning"),
            (MODULE.LayerSummary("xml", checked=2, errors=1), "error"),
        )
        for summary, expected_method in cases:
            with self.subTest(expected_method=expected_method):
                logger = mock.Mock()
                MODULE.execute_layers(
                    self.settings(layers=["xml"]),
                    self.now,
                    logger,
                    runners={"xml": lambda *_args, result=summary: result},
                )
                getattr(logger, expected_method).assert_called_once_with(
                    "summary layer=%s checked=%d stalled=%d cleaned=%d "
                    "errors=%d mode=%s limit=%s",
                    summary.name,
                    summary.checked,
                    summary.stalled,
                    summary.cleaned,
                    summary.errors,
                    "monitor",
                    "none",
                )

    def test_logging_keeps_info_in_file_and_only_warns_on_stderr(self):
        log_file = self.root / "stalled.log"
        with mock.patch.object(MODULE.logging, "basicConfig") as basic_config:
            MODULE.configure_logging(log_file)

        handlers = basic_config.call_args.kwargs["handlers"]
        stream_handler = next(
            handler
            for handler in handlers
            if type(handler) is MODULE.logging.StreamHandler
        )
        file_handler = next(
            handler
            for handler in handlers
            if isinstance(handler, MODULE.logging.FileHandler)
        )
        self.assertEqual(stream_handler.level, MODULE.logging.WARNING)
        self.assertEqual(file_handler.level, MODULE.logging.INFO)
        file_handler.close()

    def test_show_summaries_filters_non_summary_info_from_stderr(self):
        with mock.patch.object(MODULE.logging, "basicConfig") as basic_config:
            MODULE.configure_logging(None, show_summaries=True)

        handlers = basic_config.call_args.kwargs["handlers"]
        handler = handlers[0]
        self.assertEqual(handler.level, MODULE.logging.INFO)
        summary = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "summary layer=xml", (), None
        )
        finding = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "layer=xml job=1", (), None
        )
        warning = MODULE.logging.LogRecord(
            "test", MODULE.logging.WARNING, __file__, 1, "warning", (), None
        )
        database_status = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "database_status total=5", (), None
        )
        self.assertTrue(handler.filter(summary))
        self.assertTrue(handler.filter(database_status))
        self.assertFalse(handler.filter(finding))
        self.assertTrue(handler.filter(warning))

    def test_database_status_summary_counts_final_and_nonfinal_rows(self):
        statuses = argparse.Namespace(
            ACCEPTED=0,
            STARTED=1,
            PAUSED=2,
            SUCCEEDED=3,
            FAILED=4,
        )
        summary = MODULE.summarize_database_statuses(
            [(None, 2), (0, 3), (1, 5), (2, 7), (3, 11), (4, 13), (99, 17)],
            statuses,
        )
        self.assertEqual(
            summary,
            {
                "accepted": 3,
                "started": 5,
                "paused": 7,
                "succeeded": 11,
                "failed": 13,
                "null": 2,
                "total": 58,
                "final": 24,
                "nonfinal": 34,
                "other": 17,
            },
        )

    def test_database_timestamp_uses_last_update_then_start_time(self):
        start = datetime(2026, 7, 30, 8, 0)
        update = datetime(2026, 7, 30, 9, 0)
        record = argparse.Namespace(time_start=start, time_end=update)
        self.assertEqual(
            MODULE.database_last_update(record),
            update.replace(tzinfo=UTC),
        )
        record.time_end = None
        self.assertEqual(MODULE.database_last_update(record), start.replace(tzinfo=UTC))

    @unittest.skipUnless(importlib.util.find_spec("pywps"), "PyWPS is not installed")
    def test_database_guard_vetoes_recent_and_old_nonfinal_requests(self):
        pywps_config = self.root / "guard-pywps.cfg"
        database = self.root / "guard-pywps.sqlite"
        workdir = self.root / "guard-work"
        workdir.mkdir()
        pywps_config.write_text(
            "[server]\n"
            f"outputpath = {self.outputs}\n"
            f"workdir = {workdir}\n"
            "[logging]\n"
            f"database = sqlite:///{database}\n"
            "level = INFO\n",
            encoding="utf-8",
        )
        os.environ["PYWPS_CFG"] = str(pywps_config)
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS

        configuration.load_configuration([str(pywps_config)])
        recent = (self.now - timedelta(hours=1)).replace(tzinfo=None)
        session = dblog.get_session()
        session.add(
            dblog.ProcessInstance(
                uuid=JOB_UUID,
                pid=12345,
                operation="execute",
                version="1.0.0",
                time_start=recent,
                status=WPS_STATUS.STARTED,
                percent_done=10,
            )
        )
        session.commit()
        session.close()

        settings = self.access_log_settings()
        settings.database_guard = True
        settings.pywps_config = pywps_config
        self.assertEqual(
            MODULE.database_recovery_vetoes(settings, [JOB_UUID]),
            {JOB_UUID: "database-request-is-nonfinal"},
        )

        session = dblog.get_session()
        record = session.query(dblog.ProcessInstance).filter_by(uuid=JOB_UUID).one()
        record.time_start = (self.now - timedelta(hours=8)).replace(tzinfo=None)
        session.commit()
        session.close()
        self.assertEqual(
            MODULE.database_recovery_vetoes(settings, [JOB_UUID]),
            {JOB_UUID: "database-request-is-nonfinal"},
        )

        session = dblog.get_session()
        record = session.query(dblog.ProcessInstance).filter_by(uuid=JOB_UUID).one()
        record.status = WPS_STATUS.FAILED
        session.commit()
        session.close()
        self.assertEqual(
            MODULE.database_recovery_vetoes(settings, [JOB_UUID]),
            {},
        )

    @unittest.skipUnless(importlib.util.find_spec("pywps"), "PyWPS is not installed")
    def test_database_cleanup_updates_request_and_removes_queue_entry(self):
        pywps_config = self.root / "pywps.cfg"
        database = self.root / "pywps.sqlite"
        workdir = self.root / "work"
        workdir.mkdir()
        pywps_config.write_text(
            "[server]\n"
            f"outputpath = {self.outputs}\n"
            f"workdir = {workdir}\n"
            "[logging]\n"
            f"database = sqlite:///{database}\n"
            "level = INFO\n",
            encoding="utf-8",
        )
        os.environ["PYWPS_CFG"] = str(pywps_config)
        from pywps import dblog
        from pywps.response.status import WPS_STATUS

        old = (self.now - timedelta(hours=8)).replace(tzinfo=None)
        session = dblog.get_session()
        session.add(
            dblog.ProcessInstance(
                uuid=JOB_UUID,
                pid=12345,
                operation="execute",
                version="1.0.0",
                time_start=old,
                time_end=old,
                status=WPS_STATUS.STARTED,
                percent_done=10,
            )
        )
        session.add(dblog.RequestInstance(uuid=JOB_UUID, request=b"{}"))
        session.commit()
        session.close()

        settings = self.settings("cleanup", ["database"])
        settings.pywps_config = pywps_config
        summary = MODULE.run_database_layer(settings, self.now, mock.Mock())
        self.assertEqual((summary.stalled, summary.cleaned, summary.errors), (1, 1, 0))

        session = dblog.get_session()
        record = session.query(dblog.ProcessInstance).filter_by(uuid=JOB_UUID).one()
        self.assertEqual(record.status, WPS_STATUS.FAILED)
        self.assertEqual(record.percent_done, 100)
        self.assertIsNone(
            session.query(dblog.RequestInstance).filter_by(uuid=JOB_UUID).first()
        )
        session.close()


if __name__ == "__main__":
    unittest.main()
