Deploy a PyWPS Application
==========================

.. contents::
    :local:
    :depth: 2

.. note::

    You can safely try the installation using Vagrant_.

Prepare
-------

You need a recent Ansible_ version (`>=2.7`) on your local client:

.. code-block:: console

  $ ansible --version
  ansible 2.7.2


Get the Playbook
----------------

Clone this playbook from GitHub:

.. code-block:: console

    $ git clone https://github.com/bird-house/ansible-wps-playbook.git
    $ cd ansible-wps-playbook

Customize Configuration
-----------------------

Configure your PyWPS installation. See :ref:`Configuration`:

.. code-block:: console

  $ cp etc/sample-emu.yml custom.yml
  $ vim custom.yml

Running your playbook locally
-----------------------------

.. warning::

    If your system has already a Supervisor_ or a PostgreSQL_ installation, please remove them manually.

.. warning::

  Make sure your Ansible directory is not world-readable, otherwise the `ansible.cfg` file will not be read.
  See `Ansible Documentation <https://docs.ansible.com/ansible/devel/reference_appendices/config.html#cfg-in-world-writable-dir>`_.

If not already done, fetch required roles/recipes from `ansible-galaxy`:

.. code-block:: console

    $ ansible-galaxy -p roles -r requirements.yml install

Run your playbook locally:

.. code-block:: console

    $ ansible-playbook -c local -i hosts playbook.yml

.. note:: You can also use the shortcut to run both::

    $ make play
