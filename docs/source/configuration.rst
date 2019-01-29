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

.. code-block:: console

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
See ``etc/sample-multiple.yml`` configuration as example.

You can also configure a shared file-server for outputs.
See ``etc/sample-multiple-with-shared-fileserver.yml``.

Use HTTPS with Nginx
--------------------

You can enable HTTPS for the Nginx service by setting the variable::

  wps_enable_https: true

See ``etc/sample-certs.yml`` configuration as example.

By default it generates a *self-signed* certificate automatically.

You can also provide your own certificate by setting the following variables::

  ssl_certs_local_privkey_path: '/path/to/example.com.key'
  ssl_certs_local_cert_path: '/path/to/example.com.pem'

Read the `ssl-certs role <https://galaxy.ansible.com/jdauphant/ssl-certs>`_ documentation for details.

Use HTTPS with client certificate validation
--------------------------------------------

When HTTPS is enabled (see above) then *optional* client certificate validation for ESGF certificates
is also activated.

Edit the following variables to change the behaviour::

  ssl_certs_verify_client: "optional"
  ssl_certs_cacert_url: "https://github.com/ESGF/esgf-dist/raw/master/installer/certs/esgf-ca-bundle.crt"

Extend PyWPS configuration
--------------------------

This Ansible playbook has its own template for a PyWPS configuration file.
This template does not cover all options and you might want to extend it for additional configurations.
You can extend the `pywps.cfg` configuration with the `extra_config` option. Here is an example:

.. code-block:: yaml

  ---
  server_name: demowps
  wps_services:
    - name: demo
      hostname: "{{ server_name }}"
      port: 5000
      extra_config: |
        [data]
        cache_path = /tmp/cache

Use WPS with load-balancer configuration
----------------------------------------

When you use a load-balancing configuration for your WPS service, your service needs
to use the external hostname used in the load-balancer. The WPS output service still
needs to use the internal hostname for the output URL.

Please see: ``etc/sample-cp4cds_load-balancer.yml``.
