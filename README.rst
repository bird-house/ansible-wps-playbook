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

  Use Ansible to deploy a full-stack `PyWPS`_ service.

.. warning::

  This playbook is under development and currently only used to deploy an `Emu`_ PyWPS application.

Introduction
============

PyWPS Ansible Playbook can completely provision a remote server to run the full stack of `PyWPS`_, including:

* `Conda <https://conda.io/miniconda.html>`_ to manage application dependencies.
* `Nginx <http://wiki.nginx.org/Main>`_ as Web-Server.
* `Supervisor <http://supervisord.org/>`_ to start/stop and monitor services.
* ...

It will install a PyWPS application on a single host.
Nginx, Supervisor and miniconda are installed on the system.
The PyWPS application is fetched from GitHub and dependencies are installed into a Conda environment.

See the ``docs`` subdirectory or `readthedocs <http://ansible-wps-playbook.readthedocs.io/en/latest/>`_ for complete documentation.
