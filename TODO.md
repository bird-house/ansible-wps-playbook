# Review findings

The items below are findings from a review of the playbook. They are recorded
for later consideration and are not currently planned work.

## Finding: configuration cleanup is too broad

`roles/pywps/tasks/clean.yml` removes every `*.conf` file from the Nginx and
Supervisor configuration directories. On a shared host, this could remove
configuration belonging to unrelated services.

## Finding: HTTPS certificate provisioning is missing

The Nginx template references certificate and private-key files when HTTPS is
enabled, but the main playbook does not install a certificate role or otherwise
create those files. This differs from the automatic self-signed certificate
behaviour described in the README.

## Finding: SELinux is weakened globally for web services

`roles/pywps/tasks/selinux.yml` makes the complete `httpd_t` domain permissive.
This affects all processes using that SELinux domain, rather than only the
PyWPS deployment.

## Finding: Conda environments are rebuilt on every run

`roles/pywps/tasks/conda.yml` unconditionally removes and recreates each Conda
environment. This makes deployments non-idempotent and can cause avoidable
service downtime, particularly if recreation fails.

## Finding: rendered configuration may expose credentials

The rendered PyWPS configuration contains the database connection URL and
password, while the template task does not specify restrictive ownership and
permissions. A newly created file may therefore be readable by users other
than the service account.

## Finding: dependencies and application revisions are not pinned

The Galaxy roles and collections in `requirements.yml` have no versions.
Application repositories also default to the `master` branch. Consequently,
the same playbook revision may install different software over time.

## Finding: multiple-service Nginx configuration is fragile

Each generated service configuration declares the same `proxy_cache_path` and
`keys_zone`. In addition, the shared-fileserver example assigns port `5001` to
both the fileserver and one PyWPS service.

## Finding: integration-test infrastructure is stale

`tests/playbook.yml` refers to roles that are absent from the current
requirements. The idempotence test only checks that the second run did not
fail; it does not check that the run reported zero changes.

The current GitHub Actions smoke test only installs dependencies, lints the
playbook, checks its syntax, and validates a small set of defaults on localhost.
A future PR should replace the retired Docker test shim with maintained Ansible
integration infrastructure and meaningful convergence and idempotence tests.

## Review validation

The local `make test` smoke test passes with Ansible Core 2.19.
