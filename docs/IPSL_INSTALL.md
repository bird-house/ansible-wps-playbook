# Playbook v0.10.0 installation for ROOK at IPSL

Run the Ansible playbook **as root on the WPS node**.

## Preparation

Before updating an existing installation, copy the current node configuration
and the deployed ROOCS configuration to a safe place:

```sh
install -d -m 700 /root/rook-upgrade-backup
cp -p custom.yml /etc/roocs.ini /root/rook-upgrade-backup/
```

A prepared IPSL configuration is included. It is based on the `rook8` setup. Review `custom.yml` and replace every value marked `REPLACE`.

```sh
git clone https://github.com/bird-house/ansible-wps-playbook.git
cd ansible-wps-playbook

ln -s etc/rook_ipsl.yml custom.yml
vi custom.yml

make play
```

The template contains the settings that normally need to be adapted for a new node:

* host and account settings
* filesystem paths and ACLs
* Slurm resources
* contact information
* output retention

IPSL does not have a ROOCS site profile yet. Therefore, the template explicitly sets all paths required for the managed `/etc/roocs.ini`.

**Do not put secrets into Git.**

## Check the installation

First test ROOK:

```sh
smoke rook
```

### Health check

v0.10.0 provides a new health check through nginx:

```text
/health2
```

The nginx endpoint calls the ROOK `health` process, providing an external check
that ROOK itself is working. The health endpoint can include multiple checks,
such as:

* availability of the mounted filesystems used for data

## Operations and maintenance

The installed read-only operator commands help inspect and maintain the node:

* `qtop` shows active and pending Slurm jobs and their peak memory use.
* `itop` shows a compact snapshot of host infrastructure health.
* `ptop rook` shows recent and active ROOK jobs from the PyWPS database.
* `insights rook` summarizes request coverage, performance, and failures from
  the ROOK logs. Use `insights rook --failures` to include grouped failure
  messages and example job IDs.

## Migrating `custom.yml` from v0.7.0

When updating an existing installation, keep only settings that are specific to the node.

Do **not** copy old defaults for database, Gunicorn, cron, cleanup, or Conda. In particular:

* cleanup retention is now configured in **hours**
* the default explicit Conda specification is `linux-64.spec`
* `roocs_site` is no longer used
* `/etc/roocs.ini` is generated from one managed, version-coupled template

Recovery tools, incident archives, request statistics, `wps-tools`, and collectd
monitoring are installed and scheduled by default. Incident evidence is kept
for 90 days; in addition to status XML, a recovered incident may include its
scheduler dump and available job output/error logs. Override `wps_tools_*`,
timeout, or monitoring settings only when IPSL deliberately needs different
behaviour.

### One-time recovery

After the first update, the PyWPS database may still contain about 10,000 old
accepted or pending jobs. Run the recovery manually:

```sh
/opt/wps-tools/sbin/recover rook --limit 10000
```

This command processes all recovery layers. The limit caps the number of jobs
handled per layer in one run. Use a lower value and repeat the command if the
backlog should be processed in smaller batches.

If an old backlog was closed during recovery or service startup, its database
end times may reflect the day it was closed instead of the maximum possible job
runtime. After deploying this version, repair excessive failed-job timestamps
with:

```sh
/opt/wps-tools/sbin/recover rook --repair-timestamps --limit 10000
```

The command checks every failed row with start and end timestamps, and only
shortens end times beyond the applicable cap. Rows whose recovery message says
they remained accepted/pending use the configured `wps_outputs_keep_minutes`,
matching the six-hour status-document retention period by default. All other
failed rows use `job_timeout_minutes` (90 minutes by default). Successful and
non-final rows are never changed. Repairs are written in one bulk transaction
instead of committing each row separately, so large historical backlogs can be
processed efficiently.

The playbook also schedules this idempotent timestamp audit every Sunday at
03:17. It uses the same job-control lock as normal recovery, repairs at most
10,000 rows per run, and can be adjusted or disabled with the
`wps_tools_job_control_timestamp_repair_*` variables.

### Temporary work disk

IPSL's 500 GB temporary disk uses a three-hour retention period, derived from
the default 90-minute `job_timeout_minutes` plus the default 90-minute
`wps_temp_keep_margin_minutes`. Changing the job timeout therefore moves the
cleanup deadline automatically while preserving the safety margin. Sites that
need a longer window can override the margin in their inventory. Cleanup reads
the scheduler dump in each aged work directory and checks its PyWPS database
row first. Requests in any non-final state are retained; only final jobs and
orphaned dumps are removed. A directory without a trustworthy scheduler dump
is left untouched and reported instead of risking an active job.

### Slurm

Slurm now schedules jobs by both **CPU and memory**.

Before deployment, check:

* `slurm_cpus`
* the memory reserved for the host
* any per-job memory overrides

## Later updates

For normal source or configuration updates without updating the Conda
environment:

```sh
make quick
```

For tools and runtime configuration that should be updated **without restarting services**:

```sh
make live
```
