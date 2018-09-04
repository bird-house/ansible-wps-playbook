Tutorial
========

.. warning::

  To test the installation you should try it with Vagrant or Docker container. See testing.

.. contents::
    :local:
    :depth: 2

Deploy Emu PyWPS
----------------

Go step by step:

.. code-block:: sh

  # clone playbook
  $ git clone https://github.com/bird-house/ansible-wps-playbook.git
  $ cd ansible-wps-playbook

  # bootstrap
  $ bash bootstrap.sh

  # configure Emu and edit as needed
  $ cp etc/sample-emu.yml custom.yml
  $ vim custom.yml

  # run Ansible
  $ make play

  # check service
  $ supervisorctl status
  $ service nginx status

Run a WPS GetCapabilites request:
http://127.0.0.1:5000/wps?service=WPS&request=GetCapabilities
