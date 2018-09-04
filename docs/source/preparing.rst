Deploy a PyWPS Application
==========================

.. contents::
    :local:
    :depth: 2

.. note::

    You can safely try the installation using Vagrant_ or Docker. See :ref:`testing`.

.. warning::

    If your system has already a Supervisor_ installation please remove it manually.
    Ansible will install a new Supervisor service and the installation might not work properly
    with left-overs of a previous installation. The will be fixed in a later release.

Get the Playbook
----------------

Clone this playbook from GitHub:

.. code-block:: sh

    $ git clone https://github.com/bird-house/ansible-wps-playbook.git
    $ cd ansible-wps-playbook

Bootstrap
---------

Run bootstrap script (only once) to prepare your system and install Ansible_:

.. code-block:: sh

    $ bash bootstrap.sh

.. note:: If you are using Conda_ you can also install Ansible via Conda::

    $ conda install ansible

Edit Configuration
------------------

Configure your PyWPS installation. See :ref:`Configuration`:

.. code-block:: sh

  $ cp -s etc/sample-emu.yml custom.yml
  $ vim custom.yml

Run Ansible
-----------

Fetch required roles/recipes from ansible-galaxy:

.. code-block:: sh

    $ ansible-galaxy -p roles -r requirements.yml install

Run playbook:

.. code-block:: sh

    $ ansible-playbook -c local playbook.yml

.. note:: You can also use the shortcut to run both::

    $ make play
