.. _live:

Live host deployment
====================

Creating a host file
--------------------

You'll need to tell Ansible how to connect to your host.
There are multiple ways to do this. The easiest for our purposes is to create a *manifest* file.

Create a file with a name like ``myhost.cfg`` that follows the pattern:

.. code-block:: ini

    wpstest.demo ansible_ssh_user=wps ansible_ssh_host=192.168.1.100 ansible_ssh_port=5555

You may leave off the ``ansible_ssh_host`` setting if the hostname is real.
However, when doing early provisioning, it's often not available.
``ansible_ssh_port`` is only required if you want to use a non-standard ssh port.
``ansible_ssh_user`` should be the login id on the remote machine.
That user must have `sudo` rights.

Running your playbook
---------------------

.. code-block:: console

    $ ansible-playbook --ask-sudo-pass -i myhost.cfg playbook.yml

The ``--ask-sudo-pass`` option instructs Ansible to ask for your user password when it uses sudo for provisioning.
It's not required if the remote user has password-less sudo rights.
