.. _examples:

Examples
=======

.. contents::
    :local:
    :depth: 2

Cluster deployment with slurm
-----------------------------

Install Slurm cluster
=====================

Use this slurm playbook:
https://github.com/agstephens/slurm-playbook

Run playbook with Vagrant::

  $ vagrant up
  $ ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook -u vagrant --private-key=~/.vagrant.d/insecure_private_key -i inventories/vagrant-cluster.yml playbook.yml


Login to slurm master node::

  $ vagrant ssh slurmmaster

Run slurm test::

  > squeue # to view the queue
  > sbatch /root/hostname.sh # to run a job
  > squeue # to see if it is running


Install PyWPS with WPS playbook
===============================

Install PyWPS into the same cluster as slurm::

  $ cp etc/vagrant-cluster.yml custom.yml
  $ ansible-playbook -u vagrant --private-key=~/.vagrant.d/insecure_private_key -i inventory/vagrant-cluster.yml playbook.yml

Test PyWPS service
==================

Test connection:
http://192.168.50.44:5000/wps?service=WPS&version=1.0.0&request=GetCapabilities

Run async with scheduler:
http://192.168.50.44:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=sleep&storeExecuteResponse=true&status=true&DataInputs=delay=2
