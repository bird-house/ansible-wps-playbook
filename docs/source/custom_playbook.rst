Customizing the deployment
==========================

.. contents::
    :local:
    :depth: 2

Use custom.yml
--------------

You can customize the default Ansible deployment configuration.
Create a ``custom.yml`` configuration and overwrite any of the variables found in ``group_vars/all``.
There are some prepared sample configurations ``etc/sample-*.yml`` for specific deployments.
Copy one of those to get started.

You can also add your custom configurtions to the ``etc/`` folder::

  $ vim etc/custom-emu.yml
  $ ln -s etc/custom-emu.yml custom.yml

Use external Postgresql Database
--------------------------------

By default the playbook will install a postgresql database. If you want to use an
existing database you can skip the installation by setting the variable::

  db_install_postgresql: false

You need to configure then the database connection string to external database::

  wps_database: "postgresql+psycopg2://db_user:db_password@db_host:5432/pywps_db"
