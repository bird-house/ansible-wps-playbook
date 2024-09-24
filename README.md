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
It includes a role to deploy Slurm. 
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

Configure the playbook:

``` sh
cp etc/sample-vagrant.yml custom.yml
vim custom.yml
```

Run the playbook:

``` sh
make play
```

Check if WPS is running:
```sh
supervisorctl status
```

Check WPS service by getting the capabilities:

http://192.168.128.100/wps?service=wps&version=1.0.0&request=GetCapabilities

## Configuration


### Edit custom.yml

You need to customize the Ansible deployment configuration to
install your PyWPS service. Create a `custom.yml` configuration and
overwrite any of the variables found in `group_vars/all`. There are some
prepared sample configurations `etc/sample-*.yml` for specific
deployments. Copy one of those to get started.

You can also add your custom configurations to the `etc/` folder to stay
away from Git control:

``` console
$ cp etc/sample-emu.yml etc/custom-emu.yml
$ vim etc/custom-emu.yml
$ ln -s etc/custom-emu.yml custom.yml
```

### Use Conda to build identical environments

You can use Conda specification files to build identical
[environments](https://conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#building-identical-conda-environments).
The WPS service needs to have a specification file, `spec-file.txt`, in
its top level folder. You can set the following option in your
`custom.yml`:

    conda_env_use_spec: true

See an example in `etc/sample-emu-with-conda-spec.yml`.


**Warning**

This is option is currently enabled for [all]{.title-ref} configured WPS
services.

### Use sqlite Database

You can use a SQLite database with the following settings:

    db_install_postgresql: false
    db_install_sqlite: true

See an example in `etc/sample-sqlite.yml`.

### Use PostgreSQL Database installed by playbook

By default the playbook will install a PostgreSQL database. You can
customize the installation. For example you can configure a database
user:

    db_user: dbuser
    db_password: dbuser

See an example in `etc/sample-postgres.yml`.


### Use external PostgreSQL Database

If you want to use an existing database you can skip the database
installation by setting the variable:

    db_install_postgresql: false

You need to configure then the database connection string to your
external database:

    wps_database: "postgresql+psycopg2://user:password@host:5432/pywps"

See an example in `etc/sample-postgres.yml`.

### Install multiple PyWPS applications

You can install several PyWPS applications with a single Ansible run.
See `etc/sample-multiple.yml` configuration as example.

You can also configure a shared file-server for outputs. See
`etc/sample-multiple-with-shared-fileserver.yml`.

### Use HTTPS with Nginx

You can enable HTTPS for the Nginx service by setting the variable:

    wps_enable_https: true

See `etc/sample-certs.yml` configuration as example.

By default it generates a *self-signed* certificate automatically.

You can also provide your own certificate by setting the following
variables:

    ssl_certs_local_privkey_path: '/path/to/example.com.key'
    ssl_certs_local_cert_path: '/path/to/example.com.pem'

Read the [ssl-certs
role](https://galaxy.ansible.com/jdauphant/ssl-certs) documentation for
details.

### Extend PyWPS configuration

This Ansible playbook has its own template for a PyWPS configuration
file. This template does not cover all options and you might want to
extend it for additional configurations. You can extend the
`pywps.cfg` configuration with the
`extra_config` option. Here is an example:

``` yaml
---
server_name: demowps
wps_services:
  - name: demo
    hostname: "{{ server_name }}"
    port: 5000
    extra_config: |
      [data]
      cache_path = /tmp/cache
```
