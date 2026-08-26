# v0.10.0 installation for IPSL node admins

Run the playbook **as root on the WPS node**. The prepared IPSL configuration
is based on rook8; select it and replace every value marked `REPLACE`.

```sh
git clone https://github.com/bird-house/ansible-wps-playbook.git
cd ansible-wps-playbook
ln -s etc/rook_ipsl.yml custom.yml
vi custom.yml
make play
```

The template highlights the required host, account, ACL, Slurm, contact, and
retention choices. IPSL has no ROOCS profile yet, so it explicitly provides
dummy overrides for every path written to `/etc/roocs.ini`. Keep secrets
outside Git.

Test ROOK, then inspect the node with the read-only operator commands:

```sh
supervisorctl status
smoke rook
qtop
itop
ptop rook
insights rook
```

## `custom.yml`: v0.7.0 to v0.10.0

- Keep only node-specific choices. Do not copy old database, Gunicorn, cron,
  cleanup, or Conda defaults; cleanup is now configured in hours and the
  explicit Conda spec defaults to `linux-64.spec`.
- Do not carry forward `roocs_site`. One managed `/etc/roocs.ini` template now
  supplies version-coupled settings; set all IPSL paths in the prepared file.
- Recovery, incident archives, request statistics, `wps-tools`, and collectd
  monitoring are now installed and scheduled by default. Override their
  `wps_tools_*`, timeout, or monitoring variables only when the node needs a
  deliberate exception.
- Slurm now schedules by CPU **and memory**. Recheck `slurm_cpus`, the host
  memory reserve, and any per-job memory override before deployment.

For later source/configuration updates use `make quick`; for tools and runtime
configuration without service restarts use `make live`.
