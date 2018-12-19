Install Ansible
===============

.. contents::
    :local:
    :depth: 2

.. warning::
  You need to install a recent Ansible_ version (``>=2.7``).

.. note::
  You will need Ansible only on your client which you use for running the Ansible scripts.
  The server can be installed remotely.

Please read the `Ansible installation guide`_ to install Ansible on your system.
In the following we give some installation examples.

MacOS
-----

Use brew_ to install Ansible:

.. code-block:: sh

  $ brew install ansible
  # check version
  $ ansible --version
  ansible 2.7.2

Conda
-----

You can use Conda_ to install Ansbile. Conda is available for Linux, MacOS and Windows.

.. code-block:: sh

  $ conda install -c conda-forge ansible
  # check version
  $ ansible --version
  ansible 2.7.2

If you don't have Conda installed, the fastest way is to install Miniconda_, preferably the Python 3.x version.


.. _`Ansible installation guide`: https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html
.. _brew: https://brew.sh/
.. _Conda: https://conda.io/docs/user-guide/install/index.html
.. _Miniconda: https://conda.io/miniconda.html
