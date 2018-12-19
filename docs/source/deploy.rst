Deploy a PyWPS Application
==========================

.. contents::
    :local:
    :depth: 2

.. warning::

    This Ansible script has been tested on CentOS 6/7 and Ubuntu 18.04.

.. note::

    You can safely try the installation using Vagrant_ or Docker. See :ref:`testing`.


Get the Playbook
----------------

Clone this playbook from GitHub:

.. code-block:: sh

    $ git clone https://github.com/bird-house/ansible-wps-playbook.git
    $ cd ansible-wps-playbook

Edit Configuration
------------------

Configure your PyWPS installation. See :ref:`Configuration`:

.. code-block:: sh

  $ cp etc/sample-emu.yml custom.yml
  $ vim custom.yml

Run Ansible
-----------

.. warning::

    If your system has already a Supervisor_ or a PostgreSQL_ installation, please remove them manually.

.. warning::

  Make sure your Ansible directory is not world-readable, otherwise the `ansible.cfg` file will not be read.
  See `Ansible Documentation <https://docs.ansible.com/ansible/devel/reference_appendices/config.html#cfg-in-world-writable-dir>`_.

Fetch required roles/recipes from ansible-galaxy:

.. code-block:: sh

    $ ansible-galaxy -p roles -r requirements.yml install

Run playbook:

.. code-block:: sh

    $ ansible-playbook -c local playbook.yml

.. note:: You can also use the shortcut to run both::

    $ make play
