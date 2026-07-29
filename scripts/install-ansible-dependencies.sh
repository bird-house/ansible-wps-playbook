#!/usr/bin/env bash

set -euo pipefail

retry() {
    local attempt

    for attempt in 1 2 3; do
        if "$@"; then
            return
        fi
        if [[ $attempt -eq 3 ]]; then
            echo "[ERROR] Dependency installation failed after $attempt attempts" >&2
            return 1
        fi

        echo "[WARN] Dependency installation failed; retrying in $((attempt * 5)) seconds" >&2
        sleep "$((attempt * 5))"
    done
}

retry ansible-galaxy role install --force --role-file requirements.yml
retry ansible-galaxy collection install --force --requirements-file requirements.yml
