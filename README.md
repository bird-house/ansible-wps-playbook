# PyWPS Ansible Playbook

[![Checks](https://github.com/bird-house/ansible-wps-playbook/actions/workflows/checks.yml/badge.svg)](https://github.com/bird-house/ansible-wps-playbook/actions/workflows/checks.yml)
[![Docker convergence](https://github.com/bird-house/ansible-wps-playbook/actions/workflows/convergence.yml/badge.svg)](https://github.com/bird-house/ansible-wps-playbook/actions/workflows/convergence.yml)
[![GitHub license](https://img.shields.io/github/license/bird-house/ansible-wps-playbook.svg)](https://github.com/bird-house/ansible-wps-playbook/blob/master/LICENSE)

Deploy one or more [PyWPS](https://pywps.org/) applications on a single
Linux host with [Ansible](https://www.ansible.com/).

> [!WARNING]
> This playbook is under development and tailored to
> [Birdhouse](https://bird-house.github.io/) applications. Releases from
> v0.6.0 onward support single-host deployment only. For the previous Slurm
> cluster deployment, use the v0.5.x series.

The supplied `make play` command uses Ansible's local connection. Run it on
the Linux host being provisioned or inside the supplied Vagrant VM.

## What it installs

PyWPS Ansible Playbook can completely provision a server to run
the full PyWPS stack, including:

- [Conda](https://docs.conda.io/) environments for application dependencies.
- [Nginx](https://www.nginx.com/) as the web server and reverse proxy.
- [Supervisor](https://supervisord.org/) to start and monitor services.
- [PostgreSQL](https://www.postgresql.org/) as an optional job database.
- [Slurm](https://slurm.schedmd.com/) as an optional local workload manager.

Application repositories are fetched from GitHub, and each configured WPS
service receives its own Conda environment.

## Architecture

```mermaid
flowchart LR
    Client["WPS client"] --> Nginx["Nginx<br/>reverse proxy and optional TLS"]
    Nginx --> WPS["Gunicorn + PyWPS<br/>one Conda environment per service"]
    Supervisor["Supervisor"] --> WPS
    WPS --> Database["PostgreSQL or SQLite"]
    WPS --> Files["Outputs and temporary files"]
    Cron["Optional hourly cleanup cron"] --> Files
    Ansible["Ansible playbook<br/>local connection"] -. provisions .-> Nginx
    Ansible -. configures .-> Supervisor
    Ansible -. deploys .-> WPS
    Ansible -. installs .-> Cron
```

## Compatibility and test coverage

| Area | Current baseline | Coverage |
| --- | --- | --- |
| Local development | macOS Intel/Apple Silicon and Linux | Conda environment; Apple Silicon runs the x86 test through Docker emulation |
| Python and Ansible | Python 3.12, Ansible Core 2.19.11 | Pinned in `environment.yml`; lint, syntax, and assertion checks |
| Deployment target | AlmaLinux 9 x86_64 | Minimal Docker convergence in GitHub Actions; optional Vagrant sandbox |
| Debian and Ubuntu | x86_64 task path retained | No automated convergence coverage; currently best effort |

This table describes the current test baseline rather than a compatibility
guarantee. Full multi-platform convergence and idempotence testing remains
future work. Red Hat family releases older than 9 are not supported.

## Testing

### Fast local checks

On macOS or Linux, create and activate the development environment from
[`environment.yml`](environment.yml):

```sh
conda env create --file environment.yml
conda activate ansible-wps-playbook
```

Run the same fast checks used by GitHub Actions:

```sh
make test
```

This installs the external Ansible roles and collections, checks the
working-tree YAML files, runs the safety `ansible-lint` profile, and parses
the playbook with `ansible-playbook --syntax-check`. It also runs a
localhost-only assertion playbook for the default retention conversions.
It also renders representative PyWPS, Gunicorn, Supervisor, Nginx, and ROOCS
configuration. The checks do not apply the deployment playbook to the local
machine.

### Minimal deployment with Docker

With Docker running, use the same AlmaLinux 9 x86_64 convergence test as
GitHub Actions:

```sh
make convergence
```

It deploys a tiny fixture service in a systemd container and checks Nginx,
Supervisor, the health endpoint, sensitive file permissions, and a second
playbook run. Docker Desktop emulates the x86_64 image on Apple Silicon.

### Optional local VM

The [`Vagrantfile`](Vagrantfile) provides an AlmaLinux 9 sandbox for manually
trying a larger configuration. It requires Vagrant 2.4 or newer and a provider
with an x86_64-compatible box. Docker convergence is the recommended option on
Apple Silicon.

Start the VM and connect:

```sh
vagrant up
vagrant ssh
```

Vagrant installs the pinned Ansible Core version automatically. Inside the VM,
prepare an ignored configuration and apply the playbook:

```sh
sudo -i
cd /vagrant
cp etc/sample-vagrant.yml custom.yml
vim custom.yml
make play
supervisorctl status
```

The service is reachable through the VM address `192.168.128.100`. Destroy the
VM when it is no longer needed:

```sh
vagrant destroy
```

Vagrant is an optional development tool, not a CI or release requirement.
Complete ROOK deployments and smoke tests with real data remain manual release
checks.

## Dependency version policy

[`requirements.yml`](requirements.yml) pins every Galaxy role and collection
to an exact version. Normal development, CI, and deployments therefore use the
same dependencies.

Updating dependencies is an explicit, tested change:

1. Edit the versions in `requirements.yml`.
2. If Ansible Core is changing, also update `environment.yml` and run:

   ```sh
   conda env update --file environment.yml --prune
   ```

3. Reinstall the edited pins and run the complete test suite:

   ```sh
   make roles-update
   ```

4. Review and commit the version changes together.

Development configurations may follow an application branch when desired. A
deployment release should pin every `wps_services[].version` to a release tag
or commit and use an explicit Conda specification. The
[`etc/sample-production.yml`](etc/sample-production.yml) example follows this
policy.

## Release workflow

1. Pin application revisions and Conda specifications used by the deployment.
2. Update dependency versions when needed, then reinstall and test them:

   ```sh
   make roles-update
   ```

   If dependency versions did not change, run `make test` instead.

3. Test one complete ROOK deployment and its real-data smoke tests on a test
   server.
4. Move the entries in `CHANGES.md` from **Unreleased** to the new version and
   date, then add a new empty **Unreleased** section.
5. Merge the release changes and confirm GitHub Actions passes on `master`.
6. Create and push an annotated release tag:

   ```sh
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

## Configuration

### Create `custom.yml`

Create a `custom.yml` file to override variables from
[`group_vars/all.yml`](group_vars/all.yml). The playbook loads this file
automatically, and Git ignores it. Prepared configurations under
`etc/sample-*.yml` provide useful starting points.

To keep multiple local configurations, store them under `etc/custom-*.yml`
and link the active one:

```sh
cp etc/sample-emu.yml etc/custom-emu.yml
vim etc/custom-emu.yml
ln -s etc/custom-emu.yml custom.yml
```

With the desired configuration selected, run the deployment from the target
host:

```sh
make play
```

For the DKRZ ROOCS profile, clisops reads data in Dask chunks limited to
`128MiB` and splits written output at `2GB` by default. The read limit is
deliberately well below a 4 GB Slurm job limit, because a process can hold
several chunks and intermediate arrays at once. Override either setting in
`custom.yml` when a different balance between memory use, output size, and
throughput is needed:

```yaml
roocs_chunk_memory_limit: 512MiB
roocs_file_size_limit: 1GB
```

The values must be positive integer byte sizes, for example `128MiB` or `2GB`.
The read setting limits an individual chunk, not the job's total memory
allocation; the write setting controls when clisops splits output files.

ROOCS project options remain in one versioned template because they are
coupled to the deployed Rook and clisops versions. Without a profile, ordinary
path variables use conventional locations below `/data`:

```yaml
roocs_enabled: true
roocs_cmip5_path: /data/cmip5
roocs_cmip6_path: /data/CMIP6
```

A profile replaces those defaults. DKRZ deployments select the included DKRZ
path profile:

```yaml
roocs_profile: dkrz
```

Direct variables in `custom.yml` have final precedence, including when a
profile is selected:

```yaml
roocs_profile: dkrz
roocs_cmip6_path: /new/data/pool/CMIP6
```

Additional institutions can define and select another path profile while
continuing to use the same ROOCS project configuration:

```yaml
roocs_profile: ipsl
roocs_project_path_profiles:
  ipsl:
    cmip5: /ipsl/data/CMIP5
    cmip6: /ipsl/data/CMIP6
    # Define the remaining project paths here.
```

When project semantics need to change for a newer Rook or clisops release,
update the single template together with the deployed application version.

### Configure collectd monitoring

Collectd monitoring is enabled by default and supports Red Hat family version
9 hosts. The collectd package must be available from a configured repository
such as EPEL. The default configuration collects load, memory, and network
interface statistics. Disk statistics remain opt-in because they require a
deployment-specific mount point. Set
`collectd_enabled: false` to disable host monitoring entirely.

```yaml
collectd_enabled: true
collectd_load_enabled: true
collectd_disk_enabled: false
collectd_disk_mount: /mnt/ext_pywps_outputs
collectd_memory_enabled: true
collectd_interface_enabled: true
# Defaults to the interface used by the default route.
# collectd_interface: ens3
```

Collectd writes daily CSV files under `/var/lib/collectd/csv`. A systemd timer
records a readable summary at 00:10, and another compresses files older than
seven days and deletes files older than 30 days at 00:30. These settings can be
overridden independently:

```yaml
collectd_summary_enabled: true
collectd_summary_schedule: "*-*-* 00:10:00"
# Previous-hour summaries, recorded five minutes after each hour.
collectd_hourly_summary_enabled: true
collectd_hourly_summary_schedule: "*-*-* *:05:00"
collectd_cleanup_enabled: true
collectd_cleanup_schedule: "*-*-* 00:30:00"
collectd_compress_after_days: 7
collectd_keep_days: 30
```

The daily summary is written to `/var/log/collectd-daily-summary.log`. Hourly
summaries are written separately to `/var/log/collectd-hourly-summary.log`.
Inspect timer state and recent errors with `systemctl list-timers` and
`journalctl`:

```sh
systemctl status collectd-daily-summary.timer collectd-hourly-summary.timer
systemctl status collectd-cleanup.timer
journalctl -u collectd-daily-summary.service -u collectd-hourly-summary.service
journalctl -u collectd-cleanup.service
```

### Configure WPS response caching

GetCapabilities and DescribeProcess responses are cached for 10 minutes by
default. The `/health` and `/health2` endpoints use a shorter 30-second cache
so frequent load-balancer checks do not overload WPS while health information
remains reasonably fresh:

```yaml
wps_cache_valid: "10m"
wps_health_cache_valid: "30s"
```

Set these variables in `custom.yml` using an Nginx time value such as `10s`,
`1m`, or `10m`. Concurrent cache misses for the health endpoints are locked so
only one request refreshes an expired entry.

### Start from a production-style example

[`etc/sample-production.yml`](etc/sample-production.yml) demonstrates a
single-host HTTPS deployment with:

- explicit cleanup retention;
- an external PostgreSQL database;
- a pinned application revision and explicit Conda specification;
- certificate paths for an existing TLS certificate;
- conservative process limits and operator metadata.

Copy it to an ignored local file and replace every `REPLACE_WITH_*`
placeholder:

```sh
cp etc/sample-production.yml etc/custom-production.yml
vim etc/custom-production.yml
ln -s etc/custom-production.yml custom.yml
```

> [!IMPORTANT]
> Do not commit database passwords, private keys, or other deployment secrets.
> Store secrets in Ansible Vault or another untracked variables file.

### Configure output and temporary-file retention

When the cleanup cron jobs are enabled, they run hourly at minute 3 and remove
PyWPS outputs and temporary process directories older than 12 hours. Enable
the jobs and configure their retention periods with:

```yaml
cron_enabled: true
wps_outputs_keep_hours: 12
wps_temp_keep_hours: 12
```

The playbook converts the hour values to the minutes consumed by the cleanup
commands.

### Configure scheduled service restarts

PyWPS services restart daily by default when cron is enabled. Scheduled
restarts can be disabled, or the schedule changed to hourly:

```yaml
wps_restart_enabled: true
wps_restart_schedule: daily  # hourly or daily
```

Set `wps_restart_enabled: false` to disable the cron entry. The script
remains available and can still be run manually:

```sh
/var/lib/pywps/restart-pywps.sh --force SERVICE_NAME
```

### Monitor and recover stalled jobs

The playbook installs the job-control script once. Scheduled monitoring does
not change live request state. The ordered XML, database, and missing-status
polling recovery layers are enabled by default:

```yaml
cron_enabled: true
job_long_running_minutes: 1
wps_job_control_monitor_enabled: true
wps_job_control_recovery_enabled: true
wps_missing_status_recovery_enabled: true
wps_job_control_schedule:
  minute: "*/5"
  hour: "*"
wps_job_control_recovery_schedule:
  minute: "1-56/5"
  hour: "*"
wps_job_control_stale_after_minutes: 30
wps_job_control_database_stale_after_minutes: 30
wps_job_control_database_status_window_hours: 24
wps_job_control_recovery_limit: 100
wps_job_incident_archive_enabled: true
wps_job_incident_keep_days: 30
```

The monitor runs every five minutes. It does not change live request state,
but it preserves failed XML documents in the incident archive. When recovery
is enabled, one locked recovery command runs every five minutes with a
one-minute offset. It always processes XML, database, and then polling evidence;
polling recovery can still be disabled independently when it is not wanted.
The database layer reports non-final requests as long-running after one minute
by default, based on their request start time. This is an early warning only.
Its monitor summary also counts every database request started within the
configured recent window, including final requests, and reports the status mix:

```text
summary layer=database total=20 running=8 accepted=2 failed=1 success=9 ...
```

`wps_job_control_database_status_window_hours` defaults to 24 and accepts
values from 3 through 24 hours. This reporting window does not restrict stale
detection or recovery; old non-final requests remain eligible for recovery.
XML documents and database rows have separate two-hour stale thresholds. XML
recovery writes a failed status document; database recovery only reconciles
the database row and removes its stored request. Stalled database rows are
reported only as stalled, rather than being counted again as long-running.
Standard cron fields
`minute`, `hour`, `day`, `month`, and `weekday` are configurable. When the XML
layer and scheduled output cleanup are enabled, the stale threshold must be
shorter than `wps_outputs_keep_minutes`; otherwise status files could be removed
before the monitor sees them.

The playbook renders the operation switches into every service configuration:

```ini
[job_control]
monitor_enabled = true
recovery_enabled = true
missing_status_recovery_enabled = true
statistics_enabled = true
incident_archive_enabled = true
incident_archive_dir = /var/lib/pywps/job-incidents/SERVICE_NAME
```

The cron entries are controlled globally by `cron_enabled`; when invoked, the
script reads these per-service switches from `/etc/pywps/SERVICE.cfg`. Disabled
operations exit successfully without inspecting or changing jobs.

The XML and database layers run independently. The XML layer examines both
`Status@creationTime` and the file modification time, using the newer value as
the last update. Once a `ProcessStarted` document reaches the configured
long-running threshold, the XML layer inspects its matching `job-error.txt`. A
Slurm terminal cgroup OOM diagnostic makes that job recoverable without waiting
for the XML stale threshold. Other states and younger running jobs do not cause
temporary-directory scans. Other stderr content, including warnings, does not
trigger early recovery. When PyWPS labels its local wall-clock value as UTC, the
monitor recognizes the host UTC offset by its agreement with the file
modification time and corrects it; valid UTC timestamps remain unchanged. Only
`ProcessSucceeded` and `ProcessFailed` are final; every
other state older than the threshold is stalled. The database layer uses the
last database status time, falling back to the request start time. Timestamps
with `Z` are interpreted as UTC. Naive PyWPS database timestamps are matched
to the UTC creation time encoded by each version-1 job UUID. This preserves
the writer's timezone even when the monitor runs in a different timezone.
Older or non-version-1 rows fall back to the monitor host's local timezone;
the deployment host should therefore still keep its clock consistent.

Monitoring never changes live request state. Review
`/var/log/pywps/SERVICE_NAME-job-monitor.log` before enabling scheduled
recovery, or run the appropriate recovery shortcut manually.
This path is derived from the service's existing `[logging] file` setting.
The existing `/etc/logrotate.d/pywps` wildcard manages this log together with
the other PyWPS logs. By default, a log becomes eligible for rotation after
one day only when it exceeds `10M`; both the size and retained archive count
are configurable with `wps_logrotate_min_size` and
`wps_logrotate_rotations`.

Individual stalled findings and warning summaries are written to the log file.
Scheduled runs keep warnings off the console so cron does not mail them. Only
critical failures, such as an unreadable monitoring source or a failed recovery
operation, reach cron mail. Run a manual read-only check for a service with the
installed helper:

```sh
sudo /var/lib/pywps/monitor SERVICE_NAME
```

The helper checks all three layers and prints their quick summaries to the
terminal. It does not calculate the complete database status aggregate. The
five-minute scheduled monitor performs the same all-layer check and remains
quiet on the cron console unless it encounters a critical error.

Recover stalled jobs with the single ordered operator command:

```sh
sudo /var/lib/pywps/recover SERVICE_NAME
```

Recovery always runs XML, database, and polling in that order under one
per-service lock. XML recovery loads the matching `job_*.dump` from the PyWPS
work directory and asks PyWPS itself to update both the database and status
document to `ProcessFailed`. This preserves request inputs and output
definitions when lineage was enabled. Before recovery, the exact source XML
and job dump are copied to the incident archive with mode `0640`. Both follow
the configured incident-retention cleanup. The PyWPS update runs as the service
user rather than root, uses that account's home and XDG paths instead of root's
user directories, runs from the service source directory just like Gunicorn,
and disables temporary-directory cleanup.

Slurm cgroup OOM failures use the same dump-backed path, but are eligible on
the first recovery run after the long-running threshold that sees Slurm's
completed `oom-kill event(s)` marker in `job-error.txt`. The generated failure
text reports that the worker exceeded its memory allocation, allowing clients
to stop polling promptly.

Recovery fails without changing XML or database state when the dump is absent,
duplicated, changed, or does not match the UUID, work directory, or status
destination. That UUID is excluded from database recovery for the remainder of
the run. There is deliberately no lossy XML-only fallback. In the database
layer, other stale rows are marked failed and matching stored queue entries are
removed; this does not change the database schema.
Polling recovery can be disabled with its separate switch. The generated
`[job_control]` section lives in the service's existing PyWPS configuration,
and each cron entry uses that service's Conda environment and configuration.
Command-line options override those defaults, for example:

```sh
sudo /var/lib/pywps/monitor SERVICE_NAME --stale-after-minutes 720
sudo /var/lib/pywps/recover SERVICE_NAME --stale-after-minutes 720
sudo /var/lib/pywps/recover SERVICE_NAME --database-stale-after-minutes 720
sudo /var/lib/pywps/recover SERVICE_NAME --limit 500
```

The concise commands installed in `/var/lib/pywps` are `monitor`, `recover`,
and `statistics`. The deployed implementation is `pywps-job-control.py`.

The installed helpers have fixed layer scopes and reject `--layer`. For custom
diagnosis, invoke `pywps-job-control.py` with that service's deployed Conda
Python and repeat `--layer`, for example `--layer xml --layer database`.
The script validates its interpreter against the path rendered in the service
configuration and rejects the host Python. Without explicit layers, monitoring
and recovery use all three layers in their safe order; statistics uses XML and
database. `--stale-after-minutes` overrides the XML threshold, while
`--database-stale-after-minutes` overrides database cleanup.
`--limit` caps the number of stalled jobs processed in each selected layer.
The database applies a limit oldest-first in SQL, which keeps initial recovery
batches bounded even when years of unfinished requests have accumulated.
Recovery defaults to `wps_job_control_recovery_limit`, which is 100. Monitoring
remains unlimited unless `--limit` is explicitly supplied, so an old backlog
cannot hide newer stalled requests. An explicit `--limit` overrides the
configured recovery defaults for every selected layer. Polling recovery uses
its separate default of `wps_missing_status_recovery_limit`, which is 20.
The underlying `--status-counts` option enables a complete database aggregate
for explicit low-level invocations.

### Preserve failed-job evidence

Failed PyWPS status documents are copied into a separate, bounded archive
before routine output cleanup can remove them:

```yaml
wps_job_incident_archive_enabled: true
wps_job_incident_archive_dir: /var/lib/pywps/job-incidents
wps_job_incident_keep_days: 30
```

Each service has its own subdirectory. Files use UTC timestamps and searchable
names such as
`20260807T142530Z__error__rook__subset__UUID.xml`. Failures created by the
recovery layers use `recovered` instead of `error`. The complete status
document is preserved, including the process identifier, submitted inputs,
and failure message. Archive creation is atomic and idempotent; an existing
incident is never overwritten. The job-control log records the incident type
and archive path.

The archive cleanup runs hourly at minute 3 and removes incident XML older than
30 days by default. Ordinary successful status documents retain the shorter
`wps_outputs_keep_hours` period. Inspect incidents with, for example:

```sh
sudo find /var/lib/pywps/job-incidents/rook -type f -name '*.xml' -print
```

Slurm timeout enforcement and host-wide queue monitoring are described under
[Use the Slurm scheduler](#use-the-slurm-scheduler).

### Record hourly PyWPS job statistics

When cron is enabled, a separate read-only statistics job runs hourly at four
minutes past the hour by default:

```yaml
wps_job_statistics_enabled: true
wps_job_statistics_schedule:
  minute: "4"
  hour: "*"
```

It records one `current_status` line per run in
`/var/log/pywps/SERVICE_NAME-stats.log`. The line contains the complete database
aggregate using the OGC API Processes vocabulary: accepted, running,
successful, failed, dismissed, and unmapped. PyWPS `started` and `paused`
records are combined as `running`. It also reports the unique stalled-job
count, the separate XML and database stalled counts, the number of XML status
documents, and layer errors. A request stalled in both XML and the database is
counted only once in `stalled`. The line also includes the number of non-final
database requests beyond the long-running warning threshold. Routine
individual findings are excluded. Errors add extra log records, and critical
errors also reach the cron console. The
statistics log uses the same daily maximum frequency and configured
minimum-size threshold as the other service logs. Run the same report manually
with:

```sh
sudo /var/lib/pywps/statistics SERVICE_NAME
```

### Summarize historical PyWPS database activity

The read-only `db-monitor` operator command aggregates database requests for an
explicit range. It reports every job state, success rate, request and duration
statistics, successful-job runtime ranges and maximum duration, per-process
totals, and failed messages grouped with their count and first and last
occurrence:

```sh
sudo /var/lib/pywps/db-monitor rook 2026-08-01/2026-08-06
```

Date-only endpoints include the complete local calendar day. ISO timestamps,
including `Z` or explicit UTC offsets, are also accepted. Add
`--identifier orchestrate` to select one process or `--json` for structured
output. The range selects `execute` requests by their start time; non-final
requests are included, and completed-job duration uses the full elapsed time.
The command queries only a timezone-safe range candidate rather than loading
the complete PyWPS table.

### Recover repeatedly polled missing status documents

The ordered recovery run inspects recent Nginx access logs for WPS
clients repeatedly polling a status URL that returns `404`. Once the same valid
UUID has reached the configured request count and polling duration, it creates
a WPS 1.0 `ProcessFailed` status document so clients such as OWSLib can finish
instead of polling forever.

```yaml
wps_job_control_recovery_enabled: true
wps_missing_status_recovery_enabled: true
wps_missing_status_poll_window_minutes: 60
wps_missing_status_min_poll_count: 3
wps_missing_status_min_poll_duration_minutes: 35
wps_missing_status_recovery_limit: 20
wps_missing_status_access_log: /var/log/nginx/access.log
wps_missing_status_database_guard: true
```

The default recovery schedule runs every five minutes at minute 1 and
considers only requests from the preceding hour. Polling is the last
recovery layer, so
XML and database reconciliation has already completed in the same locked run.
Recovery requires at least three `GET` or `HEAD` responses
with status `404`, spanning at least 35 minutes rather than arriving in one
short burst. Only the exact output path configured for that PyWPS service and a
syntactically valid UUID filename are accepted. Only the configured active log
is inspected. Rotated logs are intentionally ignored: persistent polling
appears in the active log again and qualifies after the configured minimum
duration.

On a VM hosting multiple PyWPS services, Ansible creates one ordered recovery
cron entry per service. Each entry uses that service's `/etc/pywps/SERVICE.cfg`,
filters the shared Nginx log to its exact configured output URL path, and writes
only to its configured output directory.

The database guard is enabled by default. Every non-final database request,
regardless of age, vetoes polling recovery. Consequently, a legitimate
long-running job is not failed merely because its status document is
temporarily unavailable or Lustre is hanging. An old request still marked
active must first be reviewed and handled by the existing stalled-database
recovery. A final database request or a UUID absent from the database may be
recovered from the polling evidence.

Before writing, the job checks that the XML document is still absent. It uses
an atomic create-without-replacement operation, so a status document produced
concurrently by PyWPS wins. The generated document is owned like the service's
output directory, is mode `0644`, contains a UTC creation time and useful
failure message, and is recorded as a warning in the existing per-service
stalled-job log. Each run recovers at most 20 documents by default.

The `monitor` shortcut checks XML, database, and polling without changing live
request state. The unified `recover` command handles qualifying polling
candidates only when both recovery switches are enabled.

### Use Conda to build identical environments

By default, each WPS repository must contain the configured
`conda_env_file`, which is `environment.yml`. To create environments from
explicit Conda specifications instead, place `linux-64.spec` in each WPS
repository and set:

```yaml
conda_env_use_spec: true
```

See
[`etc/sample-emu-with-conda-spec.yml`](etc/sample-emu-with-conda-spec.yml)
for an example.

> [!NOTE]
> `conda_env_use_spec` and `conda_env_spec_file` apply to all configured WPS
> services.
>
> The former default was `spec-list.txt`. If a repository still uses that
> filename, rename the file to `linux-64.spec` or set
> `conda_env_spec_file: spec-list.txt` in the inventory as a compatibility
> override. A missing-spec failure reports this migration hint.

Additional runtime packages are installed from `wps_conda_channel` through
`wps_conda_packages`, using `--freeze-installed` to minimize changes to the
freshly created application environment. The defaults provide Gunicorn,
gevent, a Conda-linked PostgreSQL driver, and the packages used by Birdhouse
services. Small test deployments can override the list.

`wps_pip_packages` remains available for packages which are not published on
the configured Conda channel, but is empty by default. Pip is still used to
install the checked-out WPS application itself.

### Use the Slurm scheduler

Slurm support has three layers: the `galaxyproject.slurm` role installs the
local scheduler, `slurm-drmaa` provides its native DRMAA library, and the
Python `drmaa` package lets PyWPS use that library. The playbook builds the
pinned native library inside each service's Conda environment and sets
`DRMAA_LIBRARY_PATH` for the service. A small local prerequisite role installs
the Slurm development headers and manages the single-VM Munge key and service.

Enable Slurm and select scheduler mode for the service:

```yaml
slurm_enabled: true
wps_services:
  - name: example
    mode: scheduler
    drmaa_native_specification: ""
```

By default, Slurm advertises all configured CPUs but also treats memory as a
consumable resource. Twenty percent of physical RAM is reserved for the OS and
host services. Jobs that do not request memory receive an equal share of the
remaining RAM based on `slurm_cpus`, and cgroups enforce the allocation so a
worker cannot trigger a host-wide OOM. Memory-heavy deployments can raise the
default allocation; this keeps all CPUs available but admits fewer heavy jobs
at once:

```yaml
slurm_cpus: 8
slurm_system_memory_reserve_percent: 20
slurm_default_job_memory_mb: 20000
```

Jobs may request up to `slurm_max_job_memory_mb`, which defaults to all
allocatable memory on the node. The scheduler uses backfill by default so jobs
that fit currently available CPU, memory, and time can run without delaying an
earlier reservation. Changing the select or cgroup plugins restarts the Slurm
daemons; drain a production node before applying this configuration for the
first time.

Keep `slurm_drmaa_version` matched to the Slurm version installed on the host.
Changing either version or the scheduler policy should be tested with a real
job submission before production rollout. The existing PyWPS smoke tests
provide this validation when the service runs in scheduler mode.

#### Limit and monitor Slurm jobs

Slurm enforces a default and maximum runtime on the `fast` partition. Its
25-minute limit stops scheduler work before the 30-minute XML and database
stale thresholds, and before workloads observed to exhaust their memory around
30 minutes. The CDS client's three-hour limit remains an outer safeguard for
queue health rather than the normal scheduler cutoff. These limits can be
overridden independently, but the Slurm timeout must remain shorter:

```yaml
wps_job_control_stale_after_minutes: 30
slurm_job_timeout_minutes: 25
```

Slurm uses its value as both `DefaultTime` and `MaxTime`, so jobs receive the
limit even when PyWPS does not pass `--time` during submission and cannot
request a higher limit. This native enforcement does not require Slurm
accounting or `slurmdbd`.

An independent read-only monitor makes one `squeue` request for `PENDING` and
`RUNNING` jobs owned by the configured PyWPS Unix account. It is enabled by
default whenever Slurm and cron are enabled, and can still be disabled
explicitly. Because every PyWPS service uses the same account on the dedicated
VM, Ansible creates one cron entry rather than one per service.

```yaml
slurm_job_monitor_enabled: true  # enabled by default when Slurm is enabled
slurm_job_monitor_schedule:
  minute: "*/5"
  hour: "*"
slurm_job_monitor_user: wps
slurm_job_monitor_pending_critical: 20
slurm_job_monitor_alert_file: /run/pywps/slurm-red-alert.json
```

The long-running warning defaults to one minute. Both WPS job control and the
Slurm monitor inherit `job_long_running_minutes`. Either can be overridden
independently without changing the global value:

```yaml
job_long_running_minutes: 1
wps_job_control_long_running_minutes: 2
slurm_job_monitor_long_running_minutes: 5
```

The default queue threshold
becomes critical when 20 or more jobs are pending. Every run
records running, pending, total, and long-running counts in
`/var/log/pywps/slurm-job-monitor.log`, which uses the existing PyWPS log
rotation. Individual long-running jobs remain warning-level log findings and do
not cause cron mail. A full pending queue, unavailable scheduler capacity, or a
monitor execution failure is critical and does cause cron mail.

The monitor also maintains a root-owned, mode `0644` red-alert file for a
future PyWPS health check. It atomically writes JSON when the pending threshold
is reached, `squeue` or `sinfo` cannot query Slurm, the partition is unavailable,
or the single node is unusable or non-responsive. A healthy run removes the
file. Long-running jobs alone do not set the red alert. The file reports a
timestamp and one of `pending-queue-full`, `slurm-capacity-unavailable`, or
`slurm-monitor-error`; it does not automatically drain nodes or restart Slurm.

Inspect the current queue manually without making changes:

```sh
sudo /usr/local/sbin/slurm-job-monitor \
  --user wps --long-running-minutes 1 --pending-critical 20
```

For a compact live view of pending and running jobs, including each running
batch step's peak resident memory and its percentage of the requested memory,
use the interactive `slurm-top` command. It batches the accounting lookup, so
each refresh makes one `squeue` and at most one `sstat` request:

```sh
/var/lib/pywps/slurm-top
```

The command refreshes every second and defaults to `slurm_job_monitor_user`.
Pass `--user USER` to select another account, or set `SLURM_TOP_INTERVAL` to
change the refresh interval. The shortcut and its `slurm-job-status.py` backend
live alongside the other monitoring scripts in `/var/lib/pywps`. `MAX RSS` is
Slurm's high-water mark for the batch step rather than an instantaneous or
whole-job aggregate. A dash means accounting data is not yet available, which
is normal just after a job starts or finishes.

The monitor never changes or cancels jobs. Its long-running threshold indicates
elapsed runtime, not lack of progress.

### Configure the database

#### SQLite

You can use a SQLite database with the following settings:

```yaml
db_install_postgresql: false
db_install_sqlite: true
```

See [`etc/sample-sqlite.yml`](etc/sample-sqlite.yml) for an example.

#### PostgreSQL installed by the playbook

By default the playbook will install a PostgreSQL database. You can
customize the installation. For example you can configure a database
user:

```yaml
db_user: dbuser
db_password: dbuser
```

See [`etc/sample-postgres.yml`](etc/sample-postgres.yml) for an example.

#### External PostgreSQL

To use an existing database, disable the local PostgreSQL installation and
provide its connection URL:

```yaml
db_install_postgresql: false
wps_database: "postgresql+psycopg2://user:password@host:5432/pywps"
```

See [`etc/sample-postgres.yml`](etc/sample-postgres.yml) for an example.

### Install multiple PyWPS applications

You can install several PyWPS applications with a single Ansible run.
See [`etc/sample-multiple.yml`](etc/sample-multiple.yml) for an example.

You can also configure a shared file server for outputs. See
[`etc/sample-multiple-with-shared-fileserver.yml`](etc/sample-multiple-with-shared-fileserver.yml).

### Use HTTPS with Nginx

The playbook configures Nginx to use an existing certificate and private key;
it does not currently create them. Place both files on the target host and
configure their paths before enabling HTTPS:

```yaml
wps_enable_https: true
ssl_certs_cert_path: /etc/ssl/example.com/example.com.pem
ssl_certs_privkey_path: /etc/ssl/example.com/example.com.key
```

See [`etc/sample-certs.yml`](etc/sample-certs.yml) for the service
configuration.

### Extend PyWPS configuration

This Ansible playbook has its own template for a PyWPS configuration
file. This template does not cover all options and you might want to
extend it for additional configurations. You can extend the
`pywps.cfg` configuration with the
`extra_config` option. Here is an example:

```yaml
---
server_name: demowps
wps_services:
  - name: demo
    hostname: "{{ server_name }}"
    port: 5000
    extra_config: |
      [data]
      cache_path = /tmp/cache
```

## Project notes

- See [`CHANGES.md`](CHANGES.md) for release history.
- See [`TODO.md`](TODO.md) for known limitations and future work.
- Run `make help` to list the available development and deployment commands.
