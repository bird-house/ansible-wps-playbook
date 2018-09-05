.. _configuration:

Configuration
=============

.. contents::
    :local:
    :depth: 2

Edit custom.yml
---------------

You need to customize the Ansible_ deployment configuration to install your PyWPS service.
Create a ``custom.yml`` configuration and overwrite any of the variables found in ``group_vars/all``.
There are some prepared sample configurations ``etc/sample-*.yml`` for specific deployments.
Copy one of those to get started.

You can also add your custom configurations to the ``etc/`` folder to stay away from Git control:

.. code-block:: sh

  $ cp etc/sample-emu.yml etc/custom-emu.yml
  $ vim etc/custom-emu.yml
  $ ln -s etc/custom-emu.yml custom.yml

Use external PostgreSQL Database
--------------------------------

By default the playbook will install a PostgreSQL_ database. If you want to use an
existing database you can skip the installation by setting the variable::

  db_install_postgresql: false

You need to configure then the database connection string to your external database::

  wps_database: "postgresql+psycopg2://user:password@host:5432/pywps"

Install multiple PyWPS applications
-----------------------------------

You can install several PyWPS applications with a single Ansible run.
See ``etc/custom-multiple.yml`` configuration as example.

Extend PyWPS configuration
--------------------------

This Ansible playbook has its own template for a PyWPS configuration file.
This template does not cover all options and you might want to extend it for additional configurations.
You can provide your own template and add your additional options in ``custom.yml``.
Set the path to your template with the following variable::

  config_template: "{{ src_dir }}/emu/emu/templates/pywps_ansible.cfg"

The variable ``src_dir`` is the path to the install folder, usually ``/usr/local/src``.

See an example of an extended configuration in the `climaf demo`_.

.. _climaf demo: https://github.com/cp4cds/climaf-wps-demo/blob/master/climafwps/templates/pywps_ansible.cfg
