# TODO

This file contains only open work. The playbook assumes a dedicated VM owned
by the PyWPS deployment.

## Actionable work

### Tests

- Add a Debian convergence scenario after the AlmaLinux test has proved
  stable.
- Tighten the second convergence run to check idempotence for tasks intended
  to remain stable while allowing deliberate service restarts.
- Speed up Docker convergence with a prebuilt test image containing Ansible
  and Galaxy dependencies, while keeping the service deployment itself clean.

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

### Stalled-job follow-up

- Move the playbook-added runtime Conda packages into the application Conda
  specifications during the longer PyWPS environment update. In particular,
  retain the Conda-linked `psycopg2` build: the pip `psycopg2-binary` wheel's
  bundled libraries segfaulted on a fresh Python 3.13 connection.
- Add a Slurm monitoring and recovery layer after a reliable mapping between a
  WPS request UUID and its Slurm job ID has been identified.
- Detect stalled Slurm jobs as well as stalled WPS requests. Define separate,
  configurable thresholds for queued and running jobs, account for legitimate
  scheduler states and long-running workloads, and make recovery actions safe
  to repeat.
- Review and update the existing job-control setup, including submission,
  status reconciliation, cancellation, timeout handling, and retry behaviour.
  Preserve the scheduler as the source of truth while keeping WPS request
  state consistent with Slurm state.
- Collect additional job statistics for troubleshooting and capacity planning,
  including queue time, run time, completion state, failure reason, retries,
  cancellation, and resource usage where Slurm exposes it. Keep collection
  configurable and avoid unbounded growth of retained statistics.
- Consider temporary-work-directory cleanup only after it can be associated
  with a request without risking another active job's files.

### Follow-up release: modernize Slurm conservatively

- Make the AlmaLinux Slurm RPM version reproducible or at least report the
  installed version clearly during deployment.
- Update the pinned Galaxy Slurm role and `slurm-drmaa` together, keeping them
  compatible with the installed Slurm release. Validate the result with the
  existing scheduler-mode PyWPS job-submission smoke tests.
- Migrate legacy `select/cons_res` to `select/cons_tres` if supported and
  decide whether strict FIFO `sched/builtin` remains intentional.

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
