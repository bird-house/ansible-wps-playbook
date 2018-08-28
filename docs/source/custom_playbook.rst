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
