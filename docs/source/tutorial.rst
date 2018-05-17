Tutorial
========

.. warning::

  To test the installation you should try it in a docker container: :ref:`docker`.

.. contents::
    :local:
    :depth: 2

Deploy the Emu PyWPS Application
================================

Go step by step:

.. code-block:: sh

  # clone playbook
  $ git clone https://github.com/bird-house/ansible-wps-playbook.git
  $ cd ansible-wps-playbook

  # bootstrap
  $ bash bootstrap.sh

  # configure Emu and edit as needed
  $ cp sample-emu.yml custom.yml
  $ vim custom.yml

  # run Ansible
  $ make play

  # check service
  $ supervisorctl status
  $ service nginx status

Run a WPS GetCapabilites request:
http://127.0.0.1:5000/wps?service=WPS&request=GetCapabilities
