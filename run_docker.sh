#!/usr/bin/env bash
#
# Start docker container for Ansible development.
#
# Usage: [OPTIONS] ./run_docker.sh [DISTRO]
#   - distro: a supported Docker distro version (default = "centos7")

# Exit on any individual command failure.
set -e

# Pretty colors.
red='\033[0;31m'
green='\033[0;32m'
neutral='\033[0m'

# Allow environment variables to override defaults.
distro=${distro:-"ubuntu:18.04"}
port=${port:-"5000"}

## Override distro with argument
if [ $# -gt 0 ]; then
    $distro=$1
fi

## Set up vars for Docker setup.
# CentOS 7
if [ $distro = 'centos:7' ]; then
  init="/usr/lib/systemd/systemd"
  opts="--privileged --volume=/sys/fs/cgroup:/sys/fs/cgroup:ro"
# CentOS 6
elif [ $distro = 'centos:6' ]; then
  init="/sbin/init"
  opts="--privileged"
# Ubuntu 18.04
elif [ $distro = 'ubuntu:18.04' ]; then
  init="/lib/systemd/systemd"
  opts="--privileged --volume=/var/lib/docker --volume=/sys/fs/cgroup:/sys/fs/cgroup:ro"
# Ubuntu 16.04
elif [ $distro = 'ubuntu:16.04' ]; then
  init="/lib/systemd/systemd"
  opts="--privileged --volume=/var/lib/docker --volume=/sys/fs/cgroup:/sys/fs/cgroup:ro"
# Ubuntu 14.04
elif [ $distro = 'ubuntu:14.04' ]; then
  init="/sbin/init"
  opts="--privileged --volume=/var/lib/docker"
# Debian 9
elif [ $distro = 'debian:9' ]; then
  init="/lib/systemd/systemd"
  opts="--privileged --volume=/var/lib/docker --volume=/sys/fs/cgroup:/sys/fs/cgroup:ro"
# Debian 8
elif [ $distro = 'debian:8' ]; then
  init="/lib/systemd/systemd"
  opts="--privileged --volume=/var/lib/docker --volume=/sys/fs/cgroup:/sys/fs/cgroup:ro"
# Fedora 24
elif [ $distro = 'fedora:24' ]; then
  init="/usr/lib/systemd/systemd"
  opts="--privileged --volume=/var/lib/docker --volume=/sys/fs/cgroup:/sys/fs/cgroup:ro"
# Fedora 27
elif [ $distro = 'fedora:27' ]; then
  init="/usr/lib/systemd/systemd"
  opts="--privileged --volume=/var/lib/docker --volume=/sys/fs/cgroup:/sys/fs/cgroup:ro"
fi

# Run the container using the supplied OS.
printf ${green}"Starting Docker container: $distro."${neutral}"\n"
docker pull $distro
docker run  -v `pwd`:/src -w /src -p $port:$port -it --rm --name ansible $distro bash
