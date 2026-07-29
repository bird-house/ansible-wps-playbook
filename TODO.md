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

### Audit Slurm configuration conservatively

- Record `scontrol --version`, the effective `SchedulerType`, `SelectType`, and
  `SelectTypeParameters`, and `sinfo -N -l` from a working production VM before
  changing defaults.
- Check whether the legacy `select/cons_res` setting should migrate to
  `select/cons_tres` and whether strict FIFO `sched/builtin` remains
  intentional.
- Match the pinned `slurm-drmaa` release to the installed Slurm version before
  updating it.
- Remove duplicate Munge setup and make the DRMAA build idempotent without
  changing a working deployment unexpectedly.
- Add focused Slurm validation and a manual job-submission smoke test before
  changing production behaviour.

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
