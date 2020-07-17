.. _examples:

Examples
========

.. contents::
    :local:
    :depth: 2

Install Slurm cluster
---------------------

Use this slurm playbook:

https://github.com/agstephens/slurm-playbook

Run playbook with Vagrant::

  $ vagrant up
  $ ANSIBLE_HOST_KEY_CHECKING=False ansible-playbook -u vagrant --private-key=~/.vagrant.d/insecure_private_key -i inventories/vagrant-cluster.yml playbook.yml


Login to slurm master node::

  $ vagrant ssh slurmmaster

Run slurm test::

  > sudo -i # become root
  > squeue # to view the queue
  > sbatch /root/hostname.sh # to run a job
  > squeue # to see if it is running


Install Emu/PyWPS
-----------------

Use the WPS playbook:

https://github.com/bird-house/ansible-wps-playbook

Install PyWPS into the same cluster as slurm::

  $ cp etc/vagrant-cluster.yml custom.yml
  $ ansible-playbook -u vagrant --private-key=~/.vagrant.d/insecure_private_key -i inventory/vagrant-cluster.yml playbook.yml

This example is using the Emu WPS with simple test processes:

https://github.com/bird-house/emu

Test Emu WPS service
++++++++++++++++++++

Test connection:

http://192.168.50.44:5000/wps?service=WPS&version=1.0.0&request=GetCapabilities

Run "sleep" in async mode with scheduler:

http://192.168.50.44:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=sleep&storeExecuteResponse=true&status=true&DataInputs=delay=2

Install the rook/PyWPS for subsetting
-------------------------------------

This example is using the rook WPS with subsetting processes on climate model data:

https://github.com/roocs/rook

Installation is like before with slurm cluster but using a different config file::

  $ cp etc/vagrant-cluster-with-rook.yml custom.yml
  $ ansible-playbook -u vagrant --private-key=~/.vagrant.d/insecure_private_key -i inventory/vagrant-cluster.yml playbook.yml

In this example demo data is installed in a shared Vagrant folder ``.local/data``:

https://github.com/roocs/mini-esgf-data

Test rook WPS service
+++++++++++++++++++++

Test connection:

http://192.168.50.44:5000/wps?service=WPS&version=1.0.0&request=GetCapabilities

Run "subset" in async mode with scheduler and default values:

http://192.168.50.44:5000/wps?service=WPS&version=1.0.0&request=Execute&identifier=subset&storeExecuteResponse=true&status=true
