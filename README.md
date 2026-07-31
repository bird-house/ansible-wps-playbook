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

When the cleanup cron jobs are enabled, they run hourly and remove PyWPS
outputs and temporary process directories older than 12 hours. Enable the
jobs and configure their retention periods with:

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
pywps_restart_enabled: true
pywps_restart_schedule: daily  # hourly or daily
```

Set `pywps_restart_enabled: false` to disable the cron entry. The script
remains available and can still be run manually:

```sh
/var/lib/pywps/restart-pywps.sh --force SERVICE_NAME
```

### Monitor and clean stalled jobs

The playbook installs the stalled-job recovery script once. Its scheduled
entries are disabled by default and always run in read-only monitoring mode:

```yaml
cron_enabled: true
pywps_stalled_jobs_enabled: true
pywps_stalled_jobs_schedule:
  minute: "15"
  hour: "*"
pywps_stalled_jobs_age_hours: 6
pywps_stalled_jobs_layers:
  - xml
  - database
```

The default runs hourly at 15 minutes past the hour. Standard cron fields
`minute`, `hour`, `day`, `month`, and `weekday` are configurable. When the XML
layer and scheduled output cleanup are enabled, the stale threshold must be
shorter than `wps_outputs_keep_hours`; otherwise status files could be removed
before the monitor sees them.

The XML and database layers run independently. The XML layer examines both
`Status@creationTime` and the file modification time, using the newer value as
the last update. Only `ProcessSucceeded` and `ProcessFailed` are final; every
other state older than the threshold is stalled. The database layer uses the
last database status time, falling back to the request start time. Timestamps
with `Z`, and database timestamps without an offset, are interpreted as UTC;
the deployment host should therefore keep its clock and timezone consistent.

Monitoring never changes state. After reviewing
`/var/log/pywps/stalled-jobs-SERVICE_NAME.log`, clean both layers manually.
This path is derived from the service's existing `[logging] file` setting.
The existing `/etc/logrotate.d/pywps` wildcard rotates this log together with
the other PyWPS logs.

Individual stalled findings are written to the log file. Scheduled runs emit
a single warning summary for each layer containing stalled jobs, rather than
sending every matching UUID through cron mail. Clean both layers manually with:

```sh
sudo /usr/local/anaconda/envs/SERVICE_NAME/bin/python \
  /var/lib/pywps/recover-stalled-jobs.py \
  --config /etc/pywps/SERVICE_NAME.cfg cleanup
```

Cleanup atomically changes stalled XML documents to `ProcessFailed`. In the
database it marks the existing request failed and removes a matching stored
queue entry; it does not change the database schema. The generated
`[stalled_jobs]` section lives in the service's existing PyWPS configuration,
and each cron entry uses that service's Conda environment and configuration.
Command-line options override those defaults, for example:

```sh
sudo /usr/local/anaconda/envs/SERVICE_NAME/bin/python \
  /var/lib/pywps/recover-stalled-jobs.py \
  --config /etc/pywps/SERVICE_NAME.cfg monitor --layer xml
sudo /usr/local/anaconda/envs/SERVICE_NAME/bin/python \
  /var/lib/pywps/recover-stalled-jobs.py \
  --config /etc/pywps/SERVICE_NAME.cfg cleanup \
  --layer database --stale-after-hours 12
```

Slurm inspection and cleanup are intentionally deferred to a later iteration.

### Use Conda to build identical environments

By default, each WPS repository must contain the configured
`conda_env_file`, which is `environment.yml`. To create environments from
explicit Conda specifications instead, place `spec-list.txt` in each WPS
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

Keep `slurm_drmaa_version` matched to the Slurm version installed on the host.
Changing either version or the scheduler policy should be tested with a real
job submission before production rollout. The existing PyWPS smoke tests
provide this validation when the service runs in scheduler mode.

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
