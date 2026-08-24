# Changes

## Unreleased

Changes:

- preserve available `job-error.txt` and `job-output.txt` process logs alongside
  the source XML and scheduler dump during stalled-job recovery
- add a focused `make quick` deployment path which reinstalls WPS application
  sources without dependency resolution, refreshes PyWPS/ROOCS configuration
  and cron jobs, and restarts services without Conda or infrastructure roles
- provide a narrow `make live` path for safely updating maintenance tools and
  runtime configuration without restarting active services or Slurm jobs
- add an hourly XML request inspector which records per-job process, inputs,
  estimated duration, outcome, and failure details before output cleanup
- aggregate current and rotated request-inspection logs into process, input
  coverage, duration, and categorized memory/timeout failure reports
- apply Slurm, XML, and database timeouts only to actively running jobs so
  pending, accepted, queued, paused, and unknown jobs survive queue pressure
- add a read-only `db-monitor` helper for optional year, month, date, or exact
  PyWPS request time ranges, with aggregate status, duration, per-process, and
  grouped error-message reporting in text or JSON
- enable all ordered PyWPS recovery layers and all collectd monitoring except
  deployment-specific disk metrics by default
- stop Slurm jobs after 25 minutes, reconcile stale XML/database state after
  30 minutes, and recover repeatedly polled missing status documents after 35
  minutes within a one-hour access-log window
- remove inherited defaults from the example and DKRZ inventory files so they
  contain only deployment choices and intentional overrides
- keep version-coupled ROOCS project configuration in one template using DKRZ
  and conventional `/data` path defaults, optionally replace them from a DKRZ
  path profile, allow every final path variable to be overridden directly, and
  remove the obsolete CEDA and demo templates
- make the DKRZ clisops/Dask read and output file limits configurable, lowering
  the read default to 128 MiB for memory-constrained Slurm jobs while retaining
  the 2 GB output-file default
- ignore status documents removed by output retention after an XML directory
  scan, without hiding missing scheduler dumps or other recovery failures
- report the recent 24-hour database status mix in job-monitor summaries,
  including final jobs, with a configurable 3-24 hour reporting window
- use a one-minute global long-running job threshold inherited by WPS and
  Slurm while retaining independent subsystem overrides
- recover long-running `ProcessStarted` PyWPS status documents before the stale
  threshold when their scheduler error output contains Slurm's terminal cgroup
  OOM marker, without scanning temporary directories for other XML states
- schedule Slurm jobs by CPU and memory, reserve host RAM, enforce job memory
  with cgroups, and use backfill scheduling to prevent global OOM incidents
- use `linux-64.spec` as the default explicit Conda specification filename,
  with a clear migration hint when repositories still use `spec-list.txt`
- run dump-backed recovery from the service source directory so imports do not
  inherit cron's inaccessible `/root` working directory
- give dump-backed PyWPS recovery the service account's home and XDG
  environment after dropping privileges instead of retaining root's paths
- recognize naive PyWPS database timestamps across differing writer and
  monitor timezones by correlating them with version-1 job UUID timestamps
- correct PyWPS XML creation times that label the local wall clock as UTC when
  the corrected instant agrees with the status file modification time
- keep warning-level job-monitor findings in log files and reserve cron mail
  for critical queue, scheduler-capacity, monitoring, and recovery failures
- rebuild recovered status documents through the matching PyWPS scheduler dump
  so input lineage is retained, with exact pre-recovery evidence archives and
  fail-safe validation instead of lossy XML replacement
- require job control and its recovery child to use each service's deployed
  Conda Python rather than the host interpreter
- stage Slurm, XML/database, and missing-status polling limits, and avoid also
  reporting stalled database rows as long-running
- apply the configured cron mail recipient to the host-wide Slurm monitor
- make the managed cron file idempotent and harden output cleanup against
  empty service globs and concurrent file removal
- rename the PyWPS job-management configuration from `[stalled_jobs]` to
  `[job_control]`, including its Ansible variables, lock file, source names,
  tests, and documentation
- enable collectd monitoring for Red Hat family 9 hosts by default, with
  configurable load, disk, memory, and interface metrics, retained CSV data,
  and systemd-managed daily and hourly summaries
- enforce a configurable native Slurm partition timeout and add optional
  read-only `squeue`/`sinfo` monitoring for queue pressure, long-running jobs,
  scheduler capacity, and a health-check red-alert file
- express PyWPS stale-request thresholds in minutes, run monitoring and ordered
  recovery on a five-minute cadence, and retain hourly job statistics
- preserve failed and recovery-generated PyWPS status documents in a
  searchable, per-service 30-day incident archive with UTC filenames
- enable the read-only Slurm monitor by default and simplify PyWPS job control
  to the `monitor`, ordered `recover`, and `statistics` operator commands
- standardize public PyWPS role variables on the established `wps_` prefix
- reduce hourly job statistics to one current-status line including unique and
  per-layer stalled-job counts
- warn about long-running non-final WPS requests before they become stale,
  using the database request start time and a configurable 10-minute default

## 0.9.0 (2026-07-31)

Changes:

- add independent monitoring and cleanup layers for stalled PyWPS XML and
  database records
- raise maintained Ansible content to the `ansible-lint` safety profile
- add rendered-configuration tests and minimal AlmaLinux 9 CI convergence
- drop support for Red Hat family releases older than 9
- refresh Vagrant as an optional local AlmaLinux 9 sandbox
- clarify test targets and cancel superseded CI runs
- update the PostgreSQL role and maintained Ansible collections
- improve Gunicorn shutdown, Supervisor process handling, and scheduled restarts
- improve validation and diagnostics for manual smoke tests
- retry transient Ansible Galaxy dependency download failures
- make multi-service Nginx caching safe and validate configuration before restart
- make the pinned Slurm DRMAA integration explicit and verifiable
- install shared service runtime packages through Conda instead of binary pip
  wheels
- add short service-aware helpers for monitoring, XML recovery, and full
  stalled-job recovery
- add an `all` layer shortcut and show every summary in the manual monitor
- add `--hours` as a concise stalled-job threshold option
- add bounded, oldest-first stalled-job recovery batches with `--limit`
- report complete database status counts during manual monitoring
- remove obsolete deployment debug output and unreferenced helper artifacts

## 0.8.0 (2026-07-28)

Changes:

- configure cleanup retention in hours and convert it to minutes internally
- restart PyWPS services daily instead of hourly
- add the required `community.postgresql` collection
- update Rook deployment and ROOCS catalog configuration
- add local and GitHub CI lint, syntax, and smoke checks
- pin Ansible and Galaxy dependencies and add `make roles-update`
- improve the README, production example, and project TODOs
- fix cron logging and the `quick` and `clean` Make targets
- enforce pinned Galaxy role versions during installation
- reload systemd before restarting Supervisor

## 0.7.0 (2024-11-28)

Changes:

- using miniforge instead of miniconda (#156, #158, #159)
- removed outdated flamingo role (#157)
- updated cleaner cron.job ... run hourly (#154)
- added ipv6 to nginx config (#151)

## 0.6.0 (2024-04-16)

Changes:

- support AlmaLinux 8.x/9.x (#147)
- use Slurm role 
- updated docs ... use mkdocs (#148, #149)

## 0.5.1 (2024-04-15)

Changes:

-   updated cron config (#145).

## 0.5.0 (2023-11-30)

Changes:

-   updates
-   support for site specific roocs configs (#122)
-   added nginx access control (#127)
-   added smoke test runner (#132)
-   added config for gunicorn (#136)
-   added support for flamingo WPS (#140, #141)
-   added logrotate for slurm (#143)

## 0.4.0 (2020-09-22)

Changes:

-   added cleantempdir option (#107).
-   skip epel setup when not used (#106).
-   added demo mode for test data (#105).
-   fixed local deployment (#103).
-   added clean task (#102).
-   added support for slurm cluster deployment (#99, #100, #101).
-   use pip install for extra packages (#97, #98).

## 0.3.0 (2020-01-20)

Changes:

-   Skipped Twitcher role (#91)

## 0.2.3 (2020-01-08)

Changes:

-   Added Keycloak support for Twitcher (#87).
-   Fixed SSL client verification (#86).
-   Fixed postgres user config (#85).
-   Don\'t pin roles version (#84).

## 0.2.2 (2019-09-27)

Bucharest Release.

Changes:

-   Initial twitcher support (#82, #76).
-   Updated docs for DB config (#79).
-   Support conda spec (#74).
-   Fixes (#80, #81).

## 0.2.1 (2019-02-05)

Changes:

-   Configure wps user with optional UID/GID (#56).
-   Support for load-balancing configuration (#68).
-   Added a flag [wps_add_user]{.title-ref} to skip task \"wps add
    user\" (#64, #66).
-   Using [extra_config]{.title-ref} to extend the pywps configuration
    (#60, #62).
-   Updated docs (#59).
-   Several bug-fixes (#61, #65)

## 0.2.0 (2018-12-06)

Washington Release.

Changes:

-   Fixed RedHad/CentOS 6 issues (#50, #49).
-   Fixed CentOS 7 issue (#46).
-   Support HTTPS (#30, #45).
-   Fixed firewall issue (#39).
-   Support output file-service used by multiple WPS (#37).

## 0.1.1 (2018-09-19)

Changes:

-   Updated to latest version 2.0.2 of supervisor role (#31).
-   Added support for CentOS 6.x (#34).
-   PyWPS [outputurl]{.title-ref} parameter is now configurable (#36).

## 0.1.0 (2018-09-05)

This is the first release of the Ansible playbook for PyWPS.

Features:

-   Install PyWPS application with Nginx, Supervisor, Gunicorn and
    PostgreSQL.
-   Configuration options can be overwritten using a `custom.yml` file.
-   Allows the installation of multiple PyWPS applications.
-   PostgreSQL installation is optional.
