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

read_requirements() {
    local section=$1

    awk -v wanted_section="$section" '
        $1 == "roles:" || $1 == "collections:" {
            current_section = $1
            sub(/:$/, "", current_section)
            next
        }
        current_section == wanted_section && $1 == "-" && $2 == "name:" {
            if (name != "") {
                exit 1
            }
            name = $3
            next
        }
        current_section == wanted_section && name != "" && $1 == "version:" {
            version = $2
            gsub(/["\047]/, "", version)
            print name "\t" version
            name = ""
        }
        END {
            if (name != "") {
                exit 1
            }
        }
    ' requirements.yml
}

install_requirements() {
    local kind=$1
    local section=$2
    local separator=$3
    local requirements
    local name
    local version

    requirements=$(read_requirements "$section")
    if [[ -z $requirements ]]; then
        echo "[ERROR] No $section found in requirements.yml" >&2
        return 1
    fi

    while IFS=$'\t' read -r name version; do
        retry ansible-galaxy "$kind" install --force "${name}${separator}${version}"
    done <<< "$requirements"
}

install_requirements role roles ,
install_requirements collection collections :==
