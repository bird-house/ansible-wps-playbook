# Playbook v0.10.0 installation for ROOK at IPSL

Run the Ansible playbook **as root on the WPS node**.

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

First test ROOK. Then use the read-only operator commands to check the node:

```sh
smoke rook

qtop
itop
ptop rook
insights rook
```

### Health check

v0.10.0 provides a new health check through nginx:

```text
/health2
```

The nginx endpoint calls the ROOK `health` process, providing an external check that ROOK itself is working.

## Migrating `custom.yml` from v0.7.0

When updating an existing installation, keep only settings that are specific to the node.

Do **not** copy old defaults for database, Gunicorn, cron, cleanup, or Conda. In particular:

* cleanup retention is now configured in **hours**
* the default explicit Conda specification is `linux-64.spec`
* `roocs_site` is no longer used
* `/etc/roocs.ini` is generated from one managed, version-coupled template

Recovery tools, incident archives, request statistics, `wps-tools`, and collectd monitoring are installed and scheduled by default. Override `wps_tools_*`, timeout, or monitoring settings only when IPSL deliberately needs different behaviour.

### Slurm

Slurm now schedules jobs by both **CPU and memory**.

Before deployment, check:

* `slurm_cpus`
* the memory reserved for the host
* any per-job memory overrides

## Later updates

For normal source or configuration updates:

```sh
make quick
```

For tools and runtime configuration that should be updated **without restarting services**:

```sh
make live
```
