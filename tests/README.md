# Tests

Run the fast checks locally with:

```sh
make test
```

These checks lint the maintained Ansible content, validate playbook syntax,
and test defaults and rendered configuration without changing the host.

With Docker running, execute the same minimal convergence test used by GitHub:

```sh
make convergence
```

It uses an AlmaLinux 9 systemd container and deploys a tiny fixture application
instead of ROOK. It checks Nginx and Supervisor, calls the health endpoint,
verifies sensitive file permissions, and runs the playbook a second time. The
test defaults to `linux/amd64`, matching the deployment and GitHub runner;
Docker Desktop can emulate this platform on Apple Silicon.

Vagrant remains available as an optional AlmaLinux 9 x86_64 sandbox for
manually trying larger configurations. It is not part of CI.

Complete ROOK deployments and smoke tests with real data remain manual release
checks.
