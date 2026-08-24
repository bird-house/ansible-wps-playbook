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
- Consider temporary-work-directory cleanup only after it can be associated
  with a request without risking another active job's files.

### Unified job and resource view

- Add a combined `qtop` and `ptop` maintainer view covering PyWPS database
  jobs, Slurm state, and memory usage. Correlate jobs with a durable
  `PyWPS UUID -> Slurm job ID` mapping captured at submission time rather than
  relying on worker PIDs, which are not stored in the PyWPS database and are
  short-lived.
- Show live Slurm `MaxRSS` for running jobs and retain completed-job memory
  measurements for process-level median, p95, and maximum statistics. Prefer
  `sacct` when durable accounting is available; otherwise capture the final
  measurement before it disappears. Keep non-Slurm PyWPS jobs visible with
  unavailable memory reported clearly rather than inferred.

### Dataset catalog coverage view

- Add an `insights` coverage view that compares datasets used by retained
  requests with all datasets available in the Intake catalog, using the catalog
  metadata already cached in the database.
- Summarize coverage by project and dataset, including used, available, and
  unused counts, while allowing the uncovered dataset details to be inspected
  without making the default report excessively long.

### Health-check telemetry for `itop`

- Extend Rook's configurable health process to write one structured JSONL
  record for each actual health evaluation, including filesystem and especially
  Lustre mount checks. Do not write another record for every Route 53 request
  served from the health-response cache.
- Use a small stable schema containing the timestamp, service, overall state,
  and named check results with status, duration, and a short sanitized error.
  Health-log writes must be best-effort and must not delay or change the health
  response when logging fails.
- Rotate, compress, and bound retention for the health log. Teach `itop` to
  read only its latest complete record and report healthy, degraded, failed, or
  stale checks without rerunning filesystem or Lustre probes itself. Base
  staleness on the configured health evaluation/cache interval.

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
