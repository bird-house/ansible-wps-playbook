# PyWPS Ansible Playbook

[![GitHub license](https://img.shields.io/github/license/bird-house/ansible-wps-playbook.svg)](https://github.com/bird-house/ansible-wps-playbook/blob/master/LICENSE)


Use [Ansible](https://www.ansible.com/) to deploy a full-stack
[PyWPS](http://pywps.org/) service.


**Warning**

This playbook is under development and is currently only used to deploy
PyWPS applications from [Birdhouse](http://bird-house.github.io/) like
[Emu](http://emu.readthedocs.io/en/latest/).

**Warning**

The current version (>= v0.6.0) is a major update of the Ansible deployment. 
It includes as role to deploy Slurm. 
It can only be used for a single host deployment.

The deployment on a Slurm cluster is only support by the previous version v0.5.x.

## Introduction

PyWPS Ansible Playbook can completely provision a server to run
the full stack of [PyWPS](http://pywps.org/), including:

*   [Conda](https://conda.io/miniconda.html) to manage application
    dependencies.
*   [Nginx](https://www.nginx.com/) as Web-Server.
*   [Supervisor](http://supervisord.org/) to start/stop and monitor
    services.
*   [PostgreSQL](https://www.postgresql.org/) optional database used for
    job logging.
*   [Slurm](https://slurm.schedmd.com/) optional workload manager for
    jobs.

It will install a PyWPS application on a single host. Nginx, Supervisor
and miniconda are installed on the system. The PyWPS application is
fetched from GitHub and dependencies are installed into a Conda
environment.

## Testing with Vagrant

Use Vagrant to test the installation:

``` sh
vagrant up
```

Login in to VM:

``` sh
vagrant ssh
```

Become root:

``` sh
sudo -i 
```

Install ansible:

``` sh
dnf install epel-release
dnf install ansible
```

Change to the /vagrant folder:

``` sh
cd /vagrant
```

Run the playbook:

``` sh
cp etc/sample-vagrant.yml custom.yml
make play
```
