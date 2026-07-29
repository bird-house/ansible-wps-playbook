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

### Next release

- Deliver the stalled-job recovery script as the only substantial new feature.
- Limit additional changes to lightweight documentation, tests, lint fixes,
  and small cleanups that do not alter dependencies or deployment behaviour.
- Defer external-role replacements, Slurm modernization, configuration
  hardening, and architectural refactoring until after the release.
- Run the full AlmaLinux deployment and scheduler-mode PyWPS smoke tests,
  update the changelog, and prepare the release.

#### Stalled-job recovery requirements

- Add a standalone Python recovery script without changing PyWPS or Slurm.
- Define conservative, configurable criteria for identifying jobs that have
  stopped making progress and are no longer controlled by PyWPS or Slurm.
- Support a report-only mode before optionally terminating remaining
  processes or Slurm jobs and cleaning their temporary resources.
- Parse the corresponding WPS status XML, change the stalled job to a clear
  failed state, and replace the document atomically so clients stop polling.
- Log every decision and action, prevent overlapping runs, and retain enough
  information to diagnose likely causes such as Lustre problems.
- Make installation and an hourly schedule configurable, and add fixture-based
  tests for detection, XML namespaces and updates, cleanup, and failure cases.

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
