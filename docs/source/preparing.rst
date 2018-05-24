Deploy a PyWPS Application
==========================

.. warning::

    This Ansible playbook currently works only on Debian/Ubuntu.


.. contents::
    :local:
    :depth: 2

Get the Playbook
----------------

Clone this playbook from GitHub::

    $ git clone https://github.com/bird-house/ansible-wps-playbook.git
    $ cd ansible-wps-playbook

Bootstrap
---------

.. warning::

    Use a Docker container to try the installation. See the tutorial.

Run bootstrap script (only once) to prepare your system and install Ansible::

    $ bash bootstrap.sh

If you are using Conda you can also install Ansible via Conda::

    $ conda install ansible

Run Ansible
-----------

Fetch required roles/recipes from ansible-galaxy::

    $ ansible-galaxy -p roles -r requirements.yml install

Run playbook::

    $ ansible-playbook -c local playbook.yml

Or use the shortcut to run both::

    $ make play
