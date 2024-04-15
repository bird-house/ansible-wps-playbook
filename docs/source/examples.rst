.. _examples:

Examples
========

.. contents::
    :local:
    :depth: 2

Install Emu/PyWPS on localhost
-----------------------------

Use the WPS playbook:

https://github.com/bird-house/ansible-wps-playbook

Clone the playbook::

  $ git clone https://github.com/bird-house/ansible-wps-playbook.git
  $ cd ansible-wps-playbook

Optionally you can setup a Vagrant VM for testing::

  $ vagrant up --no-provision
  $ vagrant ssh
  $ sudo yum install epel-release
  $ sudo yum install ansible
  $ cd /vagrant

Install Emu on localhost (or in Vagrant VM)::

  $ cp etc/sample-emu.yml custom.yml
  $ ansible-playbook -c local -i hosts playbook.yml

This example is using the Emu WPS with simple test processes:

https://github.com/bird-house/emu

Test Emu WPS service
++++++++++++++++++++

Test connection:

http://localhost:5000/wps?service=WPS&version=1.0.0&request=GetCapabilities

Run "hello" in sync mode:

http://localhost:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=hello&DataInputs=name=Stranger


