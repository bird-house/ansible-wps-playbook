# TODO

This file separates actionable work from observations that still need
investigation. The playbook assumes a dedicated VM owned by the PyWPS
deployment.

## Actionable work

### Tests

- Add a Debian convergence scenario after the AlmaLinux test has proved
  stable.
- Tighten the second convergence run to check idempotence for tasks intended
  to remain stable while allowing deliberate service restarts.
- Speed up Docker convergence with a prebuilt test image containing Ansible
  and Galaxy dependencies, while keeping the service deployment itself clean.
- Add render cases when new optional configuration branches are introduced.

### Lint and Ansible modernization

- Raise `ansible-lint` gradually beyond the current `safety` profile.
- Re-enable disabled YAML style rules in `.yamllint.yml` as files are cleaned
  up.
- Modernize maintained roles to use fully qualified collection names and
  current Ansible module syntax.

### Review external Ansible roles

- Migrate from `geerlingguy.nginx` to the official NGINX Ansible role or
  collection because the current role is not actively maintained enough.
- Audit the remaining external roles for recent releases, supported platforms,
  security updates, and active maintenance; prefer official or
  community-supported replacements where appropriate.
- Replace roles individually, document variable migrations, and require
  AlmaLinux convergence plus production smoke tests before release.

### Patch release: remaining stalled-job edge cases

- Define a conservative fallback for database requests that have neither a
  status nor a usable start/update timestamp, so they do not remain permanent
  errors. Investigate polling evidence or another existing timestamp before
  recovery; do not introduce a database schema change or mark an undated row
  failed without a reliable minimum age signal.

### Patch release: host resource monitoring

- Inventory and review the manually installed production scripts for disk
  capacity and CPU-usage monitoring before copying them into the repository.
  Record their current paths, schedules, users, thresholds, dependencies,
  output, logging, and alert recipients so deployment preserves known-good
  behaviour.
- Manage the monitoring scripts and their scheduler entries with Ansible.
  Make installation and scheduling configurable, keep thresholds and alert
  destinations in variables rather than script source, use the existing cron
  management where appropriate, and avoid overwriting a manually installed
  script until its deployed content has been compared with the managed version.
- Add syntax and rendered-configuration tests, verify repeated Ansible runs do
  not create duplicate scheduler entries, and exercise alert/no-alert paths on
  a test host before enabling the managed scripts in production.

### Stalled-job follow-up

- Move the playbook-added runtime Conda packages into the application Conda
  specifications during the longer PyWPS environment update. In particular,
  retain the Conda-linked `psycopg2` build: the pip `psycopg2-binary` wheel's
  bundled libraries segfaulted on a fresh Python 3.13 connection.
- Add a Slurm monitoring and recovery layer after a reliable mapping between a
  WPS request UUID and its Slurm job ID has been identified.
- Consider temporary-work-directory cleanup only after it can be associated
  with a request without risking another active job's files.

### Follow-up release: modernize Slurm conservatively

- Make the AlmaLinux Slurm RPM version reproducible or at least report the
  installed version clearly during deployment.
- Update the pinned Galaxy Slurm role and `slurm-drmaa` together, keeping them
  compatible with the installed Slurm release.
- Migrate legacy `select/cons_res` to `select/cons_tres` if supported and
  decide whether strict FIFO `sched/builtin` remains intentional.
- Use the existing scheduler-mode PyWPS smoke tests as the required
  job-submission validation before releasing changed production behaviour.

### Refactor PyWPS deployment roles

- Separate host-level resources such as Nginx, PostgreSQL, Supervisor, users,
  and shared storage from individual PyWPS service configuration.
- Move the PyWPS role into a reusable standalone location, potentially its own
  Ansible collection or repository.
- Provide a simple playbook for the usual single-service deployment and a
  separate playbook for multiple services on one VM.
- Preserve the current multi-service variables during a documented migration
  period so existing deployments continue to work.

### Configuration hardening

- Add an HTTPS preflight check that verifies the configured certificate and
  private key exist before Nginx configuration is activated. Keep certificate
  provisioning and renewal as an external prerequisite unless requirements
  change.
- Remove the placeholder Supervisor password or fail clearly when a protected
  interface is enabled without an explicitly configured secure password.
- Safely encode PostgreSQL usernames and passwords containing URL-reserved
  characters when constructing the default database URL.
- Replace the broad permissive `httpd_t` SELinux setting with the smallest
  targeted policy required on AlmaLinux 9, backed by convergence testing.

## Intentional behaviour

The following findings are deliberate consequences of the dedicated-VM
deployment model and are not TODOs:

- The configuration cleanup removes existing Nginx and Supervisor `*.conf`
  files before rendering the complete desired configuration.
- Conda environments are removed and recreated on deployment so their contents
  match the declared environment or explicit specification exactly.

## Current validation

`make test` installs Galaxy dependencies, lints working-tree YAML and maintained
Ansible content, checks playbook syntax, and validates defaults and rendered
configuration on localhost. GitHub Actions also performs a minimal AlmaLinux 9
deployment with a tiny fixture application. Complete ROOK deployment and
real-data smoke tests remain manual.
