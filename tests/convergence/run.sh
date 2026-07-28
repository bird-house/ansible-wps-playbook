#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
container_name="ansible-wps-convergence-${GITHUB_RUN_ID:-local}-$$"
container_platform="${CONVERGENCE_PLATFORM:-linux/amd64}"

cleanup() {
  status=$?
  trap - EXIT

  if [[ $status -ne 0 ]]; then
    docker exec "$container_name" \
      systemctl status nginx supervisord postgresql --no-pager --full || true
    docker exec "$container_name" \
      journalctl -u nginx -u supervisord -u postgresql --no-pager -n 100 || true
    docker logs "$container_name" || true
  fi

  docker rm --force "$container_name" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

docker run \
  --detach \
  --name "$container_name" \
  --platform "$container_platform" \
  --privileged \
  --cgroupns=host \
  --volume /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --volume "$repo_root":/source:ro \
  --tmpfs /run \
  --tmpfs /run/lock \
  almalinux:9 \
  /sbin/init

docker exec "$container_name" dnf install --assumeyes \
  git \
  make \
  python3.11 \
  python3.11-pip

docker exec "$container_name" \
  python3.11 -m pip install --disable-pip-version-check ansible-core==2.19.11

docker exec "$container_name" bash -c '
  set -euo pipefail

  wait_for_tiny_wps() {
    for _attempt in {1..30}; do
      if supervisorctl status tiny | grep --quiet RUNNING \
        && curl --fail --silent http://localhost:8080/health \
          | grep --quiet "tiny-wps fixture is healthy"; then
        return
      fi
      sleep 1
    done

    supervisorctl status tiny
    curl --fail --show-error http://localhost:8080/health
    return 1
  }

  cp --archive /source /workspace
  cd /workspace
  ansible-galaxy role install --force --role-file requirements.yml
  ansible-galaxy collection install --force --requirements-file requirements.yml

  cp --recursive tests/fixtures/tiny-wps /tmp/tiny-wps-source
  chown --recursive root:root /tmp/tiny-wps-source
  git -C /tmp/tiny-wps-source init --initial-branch=main
  git -C /tmp/tiny-wps-source config user.name "CI"
  git -C /tmp/tiny-wps-source config user.email "ci@example.invalid"
  git -C /tmp/tiny-wps-source add .
  git -C /tmp/tiny-wps-source commit --message "Create convergence fixture"

  ansible-playbook \
    --inventory tests/convergence/hosts \
    --extra-vars @extra_vars.yml \
    --extra-vars @tests/convergence/vars.yml \
    playbook.yml

  nginx -t
  systemctl is-active --quiet nginx
  systemctl is-active --quiet supervisord
  systemctl is-active --quiet postgresql
  runuser --user postgres -- psql --tuples-only --command \
    "SELECT datname FROM pg_database WHERE datname = '\''pywps'\'';" \
    | grep --quiet pywps
  runuser --user postgres -- psql --tuples-only --command \
    "SELECT rolname FROM pg_roles WHERE rolname = '\''pywps'\'';" \
    | grep --quiet pywps
  wait_for_tiny_wps
  test "$(stat --format="%a:%U:%G" /etc/pywps/tiny.cfg)" = "640:root:wps"
  test "$(stat --format="%a:%U:%G" /etc/gunicorn/tiny.py)" = "640:root:wps"

  ansible-playbook \
    --inventory tests/convergence/hosts \
    --extra-vars @extra_vars.yml \
    --extra-vars @tests/convergence/vars.yml \
    --skip-tags conda \
    playbook.yml

  systemctl is-active --quiet nginx
  systemctl is-active --quiet supervisord
  systemctl is-active --quiet postgresql
  wait_for_tiny_wps
'
