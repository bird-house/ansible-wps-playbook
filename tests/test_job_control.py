from __future__ import annotations

import argparse
import importlib.util
import io
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "pywps-job-control.py"
SPEC = importlib.util.spec_from_file_location("pywps_job_control", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

WPS = "http://www.opengis.net/wps/1.0.0"
OWS = "http://www.opengis.net/ows/1.1"
UTC = timezone.utc
JOB_UUID = "123e4567-e89b-42d3-a456-426614174000"
OTHER_JOB_UUID = "223e4567-e89b-42d3-a456-426614174000"
THIRD_JOB_UUID = "323e4567-e89b-42d3-a456-426614174000"
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
            stale_after_minutes=360,
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

    def polling_settings(self, mode: str = "recover"):
        settings = self.settings(mode, ["polling"])
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
            (summary.checked, summary.stalled, summary.recovered, summary.errors),
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

    def test_recovery_changes_any_old_nonfinal_status_to_failed(self):
        self.write_status("ProcessAccepted")
        logger = mock.Mock()
        summary = MODULE.run_xml_layer(self.settings("recover"), self.now, logger)
        self.assertEqual((summary.stalled, summary.recovered, summary.errors), (1, 1, 0))
        logger.warning.assert_called_once_with(
            "layer=xml job=%s status=failed action=recovered",
            JOB_UUID,
        )

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
            "at least 360 minutes",
            status.find(f".//{{{OWS}}}ExceptionText").text,
        )

    @unittest.skipUnless(
        OWSLIB_AVAILABLE,
        "OWSLib is not installed",
    )
    def test_rewritten_xml_round_trips_as_a_pywps_execute_response(self):
        from owslib.wps import WPSExecution

        self.write_status("ProcessStarted")
        summary = MODULE.run_xml_layer(self.settings("recover"), self.now, mock.Mock())
        self.assertEqual((summary.recovered, summary.errors), (1, 0))

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
        self.assertIn("at least 360 minutes", execution.errors[0].text)

    def test_final_statuses_are_never_stalled(self):
        for state in ("ProcessSucceeded", "ProcessFailed"):
            with self.subTest(state=state):
                self.write_status(state)
                before = self.status.read_bytes()
                summary = MODULE.run_xml_layer(self.settings("recover"), self.now, mock.Mock())
                self.assertEqual(summary.stalled, 0)
                self.assertEqual(self.status.read_bytes(), before)

    def test_failed_status_is_archived_once_with_searchable_filename(self):
        self.write_status("ProcessFailed")
        settings = self.settings()
        settings.service_name = "alpha"
        settings.incident_archive_enabled = True
        settings.incident_archive_dir = self.root / "incidents"
        original = self.status.read_bytes()

        MODULE.run_xml_layer(settings, self.now, mock.Mock())
        MODULE.run_xml_layer(settings, self.now, mock.Mock())

        archived = list(settings.incident_archive_dir.glob("*.xml"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            archived[0].name,
            f"20260731T020000Z__error__alpha__test-process__{JOB_UUID}.xml",
        )
        self.assertEqual(archived[0].read_bytes(), original)
        self.assertEqual(archived[0].stat().st_mode & 0o777, 0o640)

    def test_recovered_status_is_archived_after_failure_rewrite(self):
        self.write_status("ProcessStarted")
        settings = self.settings("recover")
        settings.service_name = "alpha"
        settings.incident_archive_enabled = True
        settings.incident_archive_dir = self.root / "incidents"

        MODULE.run_xml_layer(settings, self.now, mock.Mock())

        archived = list(settings.incident_archive_dir.glob("*.xml"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            archived[0].name,
            f"20260731T100000Z__recovered__alpha__test-process__{JOB_UUID}.xml",
        )
        self.assertIn(b"stalled-job recovery", archived[0].read_bytes())

    def test_unknown_process_state_is_nonfinal(self):
        self.write_status("ProcessQueued")
        summary = MODULE.run_xml_layer(self.settings(), self.now, mock.Mock())
        self.assertEqual(summary.stalled, 1)

    def test_invalid_creation_time_is_an_error_and_is_not_recovered(self):
        self.status.write_text(status_xml("ProcessStarted", self.now).replace(
            "2026-07-31T10:00:00Z", "not-a-time"
        ), encoding="utf-8")
        before = self.status.read_bytes()
        summary = MODULE.run_xml_layer(self.settings("recover"), self.now, mock.Mock())
        self.assertEqual(summary.errors, 1)
        self.assertEqual(self.status.read_bytes(), before)

    def test_changed_status_is_not_replaced(self):
        self.write_status()
        document, tree = MODULE.read_xml_status(self.status)
        self.status.write_text(status_xml("ProcessStarted", self.now), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed before recovery"):
            MODULE.write_failed_xml(document, tree, "stalled", self.now)

    def test_access_log_recovery_creates_failure_after_repeated_recent_404s(self):
        settings = self.polling_settings()
        settings.service_name = "alpha"
        settings.incident_archive_enabled = True
        settings.incident_archive_dir = self.root / "incidents"
        settings.access_log.write_text(
            "".join(
                self.access_log_line(self.now - timedelta(minutes=minutes))
                for minutes in (12, 8, 2)
            ),
            encoding="utf-8",
        )

        logger = mock.Mock()
        summary = MODULE.run_polling_layer(settings, self.now, logger)

        self.assertEqual(
            (summary.checked, summary.stalled, summary.recovered, summary.errors),
            (1, 1, 1, 0),
        )
        document, _ = MODULE.read_xml_status(self.status)
        self.assertEqual(document.state, "ProcessFailed")
        self.assertEqual(document.creation_time, self.now)
        self.assertEqual(self.status.stat().st_mode & 0o777, 0o644)
        archived = list(settings.incident_archive_dir.glob("*.xml"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(
            archived[0].name,
            f"20260731T100000Z__recovered__alpha__unknown__{JOB_UUID}.xml",
        )
        logger.warning.assert_called_once_with(
            "layer=polling job=%s status=failed action=recovered polls=%d",
            JOB_UUID,
            3,
        )

    def test_access_log_monitor_reports_but_does_not_create_status(self):
        settings = self.polling_settings("monitor")
        settings.access_log.write_text(
            "".join(
                self.access_log_line(self.now - timedelta(minutes=minutes))
                for minutes in (12, 8, 2)
            ),
            encoding="utf-8",
        )
        summary = MODULE.run_polling_layer(settings, self.now, mock.Mock())
        self.assertEqual((summary.stalled, summary.recovered), (1, 0))
        self.assertFalse(self.status.exists())

    def test_access_log_database_veto_protects_an_active_request(self):
        settings = self.polling_settings()
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
            summary = MODULE.run_polling_layer(settings, self.now, logger)
        self.assertEqual((summary.stalled, summary.recovered), (0, 0))
        self.assertFalse(self.status.exists())
        logger.info.assert_called_with(
            "layer=polling job=%s decision=skip reason=%s polls=%d",
            JOB_UUID,
            "database-request-is-nonfinal",
            3,
        )

    def test_access_log_requires_exact_path_404_count_window_and_age(self):
        settings = self.polling_settings()
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
        summary = MODULE.run_polling_layer(settings, self.now, mock.Mock())
        self.assertEqual((summary.checked, summary.stalled, summary.recovered), (0, 0, 0))
        self.assertFalse(self.status.exists())

    def test_shared_access_log_is_filtered_for_the_configured_service(self):
        settings = self.polling_settings("monitor")
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

    def test_access_log_reader_reads_newest_lines_first(self):
        access_log = self.root / "reverse.log"
        access_log.write_text("first\nsecond\nthird\n", encoding="utf-8")

        self.assertEqual(
            list(MODULE.iter_lines_reverse(access_log, block_size=5)),
            ["third", "second", "first"],
        )

    def test_access_log_scan_stops_after_old_log_region(self):
        settings = self.polling_settings("monitor")
        ignored_prefix = "not an access log line\n" * 1000
        old_lines = "".join(
            self.access_log_line(self.now - timedelta(minutes=70))
            for _ in range(MODULE.ACCESS_LOG_OLD_LINE_STOP_COUNT)
        )
        recent_lines = "".join(
            self.access_log_line(self.now - timedelta(minutes=minutes))
            for minutes in (12, 8, 2)
        )
        settings.access_log.write_text(
            ignored_prefix + old_lines + recent_lines,
            encoding="utf-8",
        )

        with mock.patch.object(
            MODULE,
            "parse_access_log_line",
            wraps=MODULE.parse_access_log_line,
        ) as parser:
            candidates = MODULE.find_missing_status_polls(settings, self.now)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].count, 3)
        self.assertEqual(
            parser.call_count,
            MODULE.ACCESS_LOG_OLD_LINE_STOP_COUNT + 3,
        )

    def test_access_log_requires_polls_spanning_the_minimum_age(self):
        settings = self.polling_settings()
        line = self.access_log_line(self.now - timedelta(minutes=10))
        settings.access_log.write_text(line * 3, encoding="utf-8")
        summary = MODULE.run_polling_layer(settings, self.now, mock.Mock())
        self.assertEqual(summary.checked, 0)
        self.assertEqual(summary.recovered, 0)

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
        config = self.root / "alpha.cfg"
        config.write_text(
            "[server]\n"
            f"outputpath = {self.outputs}\n"
            "outputurl = https://example.test/outputs/alpha\n"
            "[logging]\n"
            f"file = {self.root / 'logs' / 'alpha.log'}\n"
            "[job_control]\n"
            "monitor_enabled = false\n"
            "recovery_enabled = true\n"
            "missing_status_recovery_enabled = true\n"
            "statistics_enabled = true\n"
            "stale_after_minutes = 360\n"
            "recovery_limit = 100\n"
            "incident_archive_enabled = true\n"
            f"incident_archive_dir = {self.root / 'incidents'}\n"
            f"access_log = {self.root / 'nginx-access.log'}\n"
            "poll_window_minutes = 45\n"
            "min_poll_count = 4\n"
            "min_poll_duration_minutes = 7\n"
            "missing_status_recovery_limit = 20\n"
            "missing_status_database_guard = true\n"
            f"lock_file = {self.root / 'configured.lock'}\n",
            encoding="utf-8",
        )
        settings = MODULE.parse_args(["--config", str(config), "monitor"])
        self.assertEqual(settings.layers, ["xml", "database", "polling"])
        self.assertEqual(settings.stale_after_minutes, 360)
        self.assertIsNone(settings.limit)
        self.assertEqual(settings.output_dir, self.outputs)
        self.assertEqual(settings.pywps_config, config)
        self.assertEqual(settings.output_url, "https://example.test/outputs/alpha")
        self.assertEqual(settings.access_log, self.root / "nginx-access.log")
        self.assertEqual(settings.poll_window_minutes, 45)
        self.assertEqual(settings.min_poll_count, 4)
        self.assertEqual(settings.min_poll_duration_minutes, 7)
        self.assertTrue(settings.database_guard)
        self.assertFalse(settings.monitor_enabled)
        self.assertTrue(settings.recovery_enabled)
        self.assertTrue(settings.missing_status_recovery_enabled)
        self.assertTrue(settings.statistics_enabled)
        self.assertTrue(settings.incident_archive_enabled)
        self.assertEqual(settings.incident_archive_dir, self.root / "incidents")
        self.assertEqual(settings.recovery_limit, 100)
        self.assertEqual(settings.missing_status_recovery_limit, 20)
        self.assertEqual(settings.service_name, "alpha")
        self.assertEqual(
            settings.log_file,
            self.root / "logs" / "alpha-job-monitor.log",
        )

        overridden = MODULE.parse_args([
            "--config",
            str(config),
            "recover",
            "--layer",
            "xml",
            "--stale-after-minutes",
            "720",
        ])
        self.assertEqual(overridden.layers, ["xml"])
        self.assertEqual(overridden.stale_after_minutes, 720)
        self.assertIsNone(overridden.limit)

        recovery = MODULE.parse_args(["--config", str(config), "recover"])
        self.assertEqual(recovery.layers, ["xml", "database", "polling"])

        selected_layers = MODULE.parse_args([
            "--config",
            str(config),
            "monitor",
            "--layer",
            "xml",
            "--layer",
            "database",
            "--show-summaries",
            "--status-counts",
        ])
        self.assertEqual(selected_layers.layers, ["xml", "database"])
        self.assertTrue(selected_layers.show_summaries)
        self.assertTrue(selected_layers.status_counts)

        statistics = MODULE.parse_args([
            "--config",
            str(config),
            "statistics",
        ])
        self.assertEqual(statistics.layers, ["xml", "database"])
        self.assertTrue(statistics.status_counts)
        self.assertEqual(
            statistics.log_file,
            self.root / "logs" / "alpha-stats.log",
        )

        overridden_monitor = MODULE.parse_args([
            "--config",
            str(config),
            "monitor",
            "--stale-after-minutes",
            "210",
            "--limit",
            "500",
        ])
        self.assertEqual(overridden_monitor.stale_after_minutes, 210)
        self.assertEqual(overridden_monitor.limit, 500)

    def test_configuration_requires_job_control_section(self):
        config = self.root / "old-section.cfg"
        config.write_text("[stalled_jobs]\nlayers = xml\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, r"missing \[job_control\] section"):
            MODULE.parse_args(["--config", str(config), "monitor"])

    def test_removed_command_aliases_are_rejected(self):
        for arguments in (
            ["cleanup"],
            ["monitor", "--layer", "access-log"],
            ["monitor", "--layer", "all"],
            ["monitor", "--hours", "3.5"],
            ["monitor", "--stale-after-hours", "6"],
            ["monitor", "--min-poll-age-minutes", "5"],
        ):
            with self.subTest(arguments=arguments), mock.patch(
                "sys.stderr", new=io.StringIO()
            ):
                with self.assertRaises(SystemExit):
                    MODULE.parse_args(arguments)

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

    def test_operation_flags_select_monitor_recovery_and_statistics(self):
        settings = self.settings("monitor", ["xml", "database"])
        settings.monitor_enabled = False
        self.assertFalse(MODULE.operation_is_enabled(settings))
        settings.monitor_enabled = True
        self.assertTrue(MODULE.operation_is_enabled(settings))

        settings.mode = "recover"
        settings.recovery_enabled = False
        self.assertFalse(MODULE.operation_is_enabled(settings))
        settings.recovery_enabled = True
        self.assertTrue(MODULE.operation_is_enabled(settings))

        settings.layers = ["polling"]
        settings.missing_status_recovery_enabled = False
        self.assertFalse(MODULE.layer_is_enabled(settings, "polling"))
        self.assertTrue(MODULE.operation_is_enabled(settings))
        settings.missing_status_recovery_enabled = True
        self.assertTrue(MODULE.layer_is_enabled(settings, "polling"))

        settings.mode = "statistics"
        settings.statistics_enabled = False
        self.assertFalse(MODULE.operation_is_enabled(settings))
        settings.statistics_enabled = True
        self.assertTrue(MODULE.operation_is_enabled(settings))

    def test_disabled_polling_recovery_does_not_skip_other_layers(self):
        settings = self.settings("recover", ["xml", "polling"])
        settings.missing_status_recovery_enabled = False
        xml_runner = mock.Mock(return_value=MODULE.LayerSummary("xml", checked=1))
        polling_runner = mock.Mock(return_value=MODULE.LayerSummary("polling"))
        logger = mock.Mock()

        summaries = MODULE.execute_layers(
            settings,
            self.now,
            logger,
            runners={"xml": xml_runner, "polling": polling_runner},
        )

        xml_runner.assert_called_once()
        polling_runner.assert_not_called()
        self.assertEqual([summary.name for summary in summaries], ["xml"])
        logger.info.assert_any_call(
            "layer=%s result=skip reason=recovery-disabled", "polling"
        )

    def test_recovery_uses_fixed_layer_order_and_independent_limits(self):
        settings = self.settings("recover", ["polling", "database", "xml"])
        settings.missing_status_recovery_enabled = True
        calls = []

        def record(layer):
            def runner(received, *_args):
                calls.append((layer, received.limit))
                return MODULE.LayerSummary(layer)

            return runner

        MODULE.execute_layers(
            settings,
            self.now,
            mock.Mock(),
            runners={layer: record(layer) for layer in settings.layers},
        )

        self.assertEqual(calls, [("xml", 100), ("database", 100), ("polling", 20)])

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
                    "summary layer=%s checked=%d stalled=%d recovered=%d "
                    "errors=%d mode=%s limit=%s",
                    summary.name,
                    summary.checked,
                    summary.stalled,
                    summary.recovered,
                    summary.errors,
                    "monitor",
                    "none",
                )

    def test_logging_keeps_info_in_file_and_only_warns_on_stderr(self):
        log_file = self.root / "stalled.log"
        with mock.patch.object(MODULE.logging, "basicConfig") as basic_config:
            MODULE.configure_logging(log_file, service_name="alpha")

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
        self.assertEqual(
            basic_config.call_args.kwargs["format"],
            "%(asctime)s %(levelname)s service=%(service)s %(message)s",
        )
        record = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "message", (), None
        )
        self.assertTrue(stream_handler.filter(record))
        self.assertEqual(record.service, "alpha")
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
        status_summary = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "status_summary total=5", (), None
        )
        self.assertTrue(handler.filter(summary))
        self.assertTrue(handler.filter(status_summary))
        self.assertFalse(handler.filter(finding))
        self.assertTrue(handler.filter(warning))

    def test_statistics_log_keeps_only_summaries_status_counts_and_warnings(self):
        log_file = self.root / "statistics.log"
        with mock.patch.object(MODULE.logging, "basicConfig") as basic_config:
            MODULE.configure_logging(log_file, statistics_only=True)

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
        self.assertEqual(stream_handler.level, MODULE.logging.ERROR)
        summary = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "summary layer=xml", (), None
        )
        finding = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "layer=xml job=1", (), None
        )
        status_summary = MODULE.logging.LogRecord(
            "test", MODULE.logging.INFO, __file__, 1, "status_summary total=5", (), None
        )
        self.assertTrue(file_handler.filter(summary))
        self.assertTrue(file_handler.filter(status_summary))
        self.assertFalse(file_handler.filter(finding))
        file_handler.close()

    def test_database_status_summary_uses_ogc_api_processes_vocabulary(self):
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
                "total": 58,
                "accepted": 3,
                "running": 12,
                "successful": 11,
                "failed": 13,
                "dismissed": 0,
                "unmapped": 19,
            },
        )

    def test_wps_xml_states_map_to_ogc_api_processes_statuses(self):
        self.assertEqual(MODULE.xml_job_status("ProcessAccepted"), "accepted")
        self.assertEqual(MODULE.xml_job_status("ProcessStarted"), "running")
        self.assertEqual(MODULE.xml_job_status("ProcessPaused"), "running")
        self.assertEqual(MODULE.xml_job_status("ProcessSucceeded"), "successful")
        self.assertEqual(MODULE.xml_job_status("ProcessFailed"), "failed")
        self.assertEqual(MODULE.xml_job_status("ProcessQueued"), "running")

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

        settings = self.polling_settings()
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
    def test_database_recovery_handles_started_and_null_status_requests(self):
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
        from pywps import configuration, dblog
        from pywps.response.status import WPS_STATUS

        configuration.load_configuration([str(pywps_config)])
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
        session.add(
            dblog.ProcessInstance(
                uuid=THIRD_JOB_UUID,
                pid=34567,
                operation="execute",
                version="1.0.0",
                time_start=old,
                status=None,
                percent_done=0,
            )
        )
        session.add(dblog.RequestInstance(uuid=THIRD_JOB_UUID, request=b"{}"))
        session.add(
            dblog.ProcessInstance(
                uuid=OTHER_JOB_UUID,
                pid=23456,
                operation="execute",
                version="1.0.0",
                time_start=(self.now - timedelta(hours=1)).replace(tzinfo=None),
                status=WPS_STATUS.STARTED,
                percent_done=10,
            )
        )
        session.commit()
        session.close()

        settings = self.settings("recover", ["database"])
        settings.pywps_config = pywps_config
        logger = mock.Mock()
        summary = MODULE.run_database_layer(settings, self.now, logger)
        self.assertEqual(
            (summary.checked, summary.stalled, summary.recovered, summary.errors),
            (2, 2, 2, 0),
        )
        logger.info.assert_any_call(
            "layer=database job=%s status=%s finding=stalled updated=%s",
            THIRD_JOB_UUID,
            "unmapped",
            old.replace(tzinfo=UTC).isoformat(),
        )
        self.assertEqual(
            logger.warning.call_args_list,
            [
                mock.call(
                    "layer=database job=%s status=failed action=recovered",
                    JOB_UUID,
                ),
                mock.call(
                    "layer=database job=%s status=failed action=recovered",
                    THIRD_JOB_UUID,
                ),
            ],
        )

        session = dblog.get_session()
        record = session.query(dblog.ProcessInstance).filter_by(uuid=JOB_UUID).one()
        self.assertEqual(record.status, WPS_STATUS.FAILED)
        self.assertEqual(record.percent_done, 100)
        self.assertIsNone(
            session.query(dblog.RequestInstance).filter_by(uuid=JOB_UUID).first()
        )
        null_status = (
            session.query(dblog.ProcessInstance).filter_by(uuid=THIRD_JOB_UUID).one()
        )
        self.assertEqual(null_status.status, WPS_STATUS.FAILED)
        self.assertEqual(null_status.percent_done, 100)
        self.assertIsNone(
            session.query(dblog.RequestInstance).filter_by(uuid=THIRD_JOB_UUID).first()
        )
        recent = session.query(dblog.ProcessInstance).filter_by(uuid=OTHER_JOB_UUID).one()
        self.assertEqual(recent.status, WPS_STATUS.STARTED)
        session.close()


if __name__ == "__main__":
    unittest.main()
