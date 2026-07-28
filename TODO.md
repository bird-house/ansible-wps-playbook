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
- Add render cases when new optional configuration branches are introduced.

### Lint and Ansible modernization

- Raise `ansible-lint` gradually beyond the current `safety` profile.
- Re-enable disabled YAML style rules in `.yamllint.yml` as files are cleaned
  up.
- Modernize maintained roles to use fully qualified collection names and
  current Ansible module syntax.

### Make multi-service Nginx configuration safe

- Give each service a unique `proxy_cache_path` and `keys_zone`, or declare one
  shared cache only once.
- Remove the port collision between the file server and the second WPS service
  in `etc/sample-multiple-with-shared-fileserver.yml`.
- Add an `nginx -t` validation step before reloading Nginx.

## Findings to investigate

These observations look suspicious, but the intended behaviour or best fix
should be confirmed first.

### HTTPS certificate lifecycle

HTTPS expects certificate and private-key files to exist on the target host.
Decide whether certificate provisioning remains an external prerequisite or
whether the playbook should install and renew certificates. At minimum, add a
clear preflight check before generating the Nginx configuration.

### SELinux scope

`roles/pywps/tasks/selinux.yml` makes the complete `httpd_t` domain permissive.
Confirm which access is actually required and whether targeted policy rules can
replace the broad exception.

### Supervisor credentials

The defaults include `supervisor_password: test`, while password protection and
the inet interface are disabled by default. Confirm that enabling either
interface cannot expose this placeholder credential.

### Database URL encoding

The PostgreSQL URL is assembled directly from `db_user` and `db_password`.
Check how credentials containing URL-reserved characters such as `@`, `:`, or
`/` should be encoded.

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
