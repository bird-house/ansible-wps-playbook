Deploy a PyWPS Application
==========================

.. warning::

    This Ansible playbook currently works only on Debian/Ubuntu.


Bootstrap
---------

Run bootstrap script (only once) to prepare your system and install Ansible::

    $ bash bootstrap.sh

Run Ansible
-----------

Fetch required roles/recipes from ansible-galaxy::

    $ ansible-galaxy -p roles -r requirements.yml install

Run playbook::

    $ ansible-playbook -c local playbook.yml

Or use the shortcut to run both::

    $ make play
