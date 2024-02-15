======================
PyWPS Ansible Playbook
======================

.. image:: https://img.shields.io/badge/docs-latest-brightgreen.svg
   :target: http://ansible-wps-playbook.readthedocs.org/en/latest/?badge=latest
   :alt: Documentation Status

.. image:: https://img.shields.io/github/license/bird-house/ansible-wps-playbook.svg
    :target: https://github.com/bird-house/ansible-wps-playbook/blob/master/LICENSE.txt
    :alt: GitHub license

.. image:: https://badges.gitter.im/bird-house/birdhouse.svg
    :target: https://gitter.im/bird-house/birdhouse?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge
    :alt: Join the chat at https://gitter.im/bird-house/birdhouse

.. admonition:: Description

  Use Ansible_ to deploy a full-stack PyWPS_ service.

.. warning::

  This playbook is under development and is currently only used to deploy PyWPS applications from Birdhouse_ like Emu_.

Introduction
============

PyWPS Ansible Playbook can completely provision a remote server to run the full stack of PyWPS_, including:

* Conda_ to manage application dependencies.
* Nginx_ as Web-Server.
* Supervisor_ to start/stop and monitor services.
* PostgreSQL_ optional database used for job logging.
* Slurm_ optional workload manager for jobs.

It will install a PyWPS application on a single host.
Nginx, Supervisor and miniconda are installed on the system.
The PyWPS application is fetched from GitHub and dependencies are installed into a Conda environment.

See the ``docs`` subdirectory or `readthedocs <http://ansible-wps-playbook.readthedocs.io/en/latest/>`_ for complete documentation.

.. _Birdhouse: http://bird-house.github.io/
.. _PyWPS: http://pywps.org/
.. _Emu: http://emu.readthedocs.io/en/latest/
.. _Ansible: https://www.ansible.com/
.. _Vagrant: https://www.vagrantup.com/
.. _Conda: https://conda.io/miniconda.html
.. _Nginx: https://www.nginx.com/
.. _Supervisor: http://supervisord.org/
.. _PostgreSQL: https://www.postgresql.org/
.. _Slurm: https://slurm.schedmd.com/

Testing with Vagrant
====================

Use Vagrant to test the installation:

.. code-block:: sh

    vagrant up

Login in to VM:

.. code-block:: sh

    vagrant ssh

Become root:

.. code-block:: sh

    sudo -i 

Install ansible:

.. code-block:: sh

    dnf install epel-release
    dnf install ansible


Change to the /vagrant folder:

.. code-block:: sh
  
    cd /vagrant

Run the playbook:

.. code-block:: sh

    cp etc/sample-vagrant.yml custom.yml
    make play