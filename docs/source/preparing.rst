.. _requirements:

Requirements
============

.. contents::
    :local:
    :depth: 2

Target Server
-------------

Supported platforms
~~~~~~~~~~~~~~~~~~~

At the moment, we are testing with CentOS 6/7 and Ubuntu 18.04.

SSH access; sudo
~~~~~~~~~~~~~~~~

Beyond the basic platform, the only requirements are that you have ``ssh`` access
to the remote server with full ``sudo`` rights.

For local testing via virtual machine, any machine that supports VirtualBox/Vagrant
should be adequate.

Local setup
-----------

.. note::
  You will need Ansible only on your client which you use for running the Ansible scripts.
  The server can be installed remotely.

On your local machine (the one from which you're controlling the remote server),
you will need a recent copy of Ansible (`>=2.7`). `docs.ansible.com`_
has thorough installation instructions.

.. warning::
  Don't us your OS package manager to install Ansible; you may get an unusably out-of-date version.

You will also nearly certainly want `git`, both for cloning the playbook and for version-controlling your own work.

To clone the playbook, use the command:

.. code-block:: console

    $ git clone https://github.com/bird-house/ansible-wps-playbook.git


Quick setup
-----------

In the following we give some installation examples.

MacOS
~~~~~

Use brew_ to install Ansible:

.. code-block:: console

  $ brew install git
  $ brew install ansible
  # check version
  $ ansible --version
  ansible 2.7.2

Conda
~~~~~

You can use Conda_ to install Ansible. Conda is available for Linux, MacOS and Windows.

.. code-block:: console

  $ conda install -c conda-forge ansible
  # check ansible version
  $ ansible --version
  ansible 2.7.2

If you don't have Conda installed, the fastest way is to install Miniconda_, preferably the Python 3.x version.

Ansible role requirements
-------------------------

We have a several Ansible role dependencies which you may fulfill via Ansible Galaxy with the command:

.. code-block:: console

    $ ansible-galaxy -r requirements.yml -p roles install

This should be executed in your playbook directory.
Downloaded requirements will be dropped into the ``roles`` directory there.

Remote setup
------------

Ansible requires that the target server have a recent Python 2.x on the server.
Newer platforms (like Ubuntu Xenial and later) may not have this activated on pristine new machines.

If you get connection errors from Ansible, check the remote machine to make sure Python 2.7 is available.
`which python2.7` will let you know.
If it's missing, use your package manager to install it.

On Ubuntu Xenial (16.0.4 LTS), `sudo apt-get install -y python` will do the trick.


.. _`docs.ansible.com`: http://docs.ansible.com/intro_installation.html
.. _brew: https://brew.sh/
.. _Conda: https://conda.io/docs/user-guide/install/index.html
.. _Miniconda: https://conda.io/miniconda.html
