.. _testing:

Testing
=======

.. contents::
    :local:
    :depth: 2

Test Ansible with Vagrant
-------------------------

Install Vagrant
+++++++++++++++

You need to install Vagrant_. See the following links for details:

* https://docs.ansible.com/ansible/latest/scenario_guides/guide_vagrant.html
* https://www.vagrantup.com/intro/getting-started/index.html
* https://blog.scriptmyjob.com/creating-an-ansible-testing-environment-using-vagrant-on-macos/

In short, you can install Vagrant on macOS with `Homebrew <https://brew.sh/>`_
(and `Homebrew Cask <https://caskroom.github.io/>`_):

.. code-block:: sh

  $ brew cask install virtualbox
  $ brew cask install vagrant

You need Ansible locally installed:

.. code-block:: sh

  $ conda install -c conda-forge ansible
  OR
  $ brew install ansible # macOS only

Install Ansible roles:

.. code-block:: sh

  $ ansible-galaxy install -p roles -r requirements.yml --ignore-errors

Run Vagrant
+++++++++++

Use Vagrant config:

.. code-block:: sh

  $ ln -s etc/sample-vagrant.yml custom.yml

Initial setup:

.. code-block:: sh

  $ vagrant up

Provision with Ansible again:

.. code-block:: sh

  $ vagrant provision

Login with SSH:

.. code-block:: sh

  $ vagrant ssh

Run Ansible manually:

.. code-block:: sh

  $ ansible-playbook -i .vagrant/provisioners/ansible/inventory/vagrant_ansible_inventory playbook.yml

Remove VMs:

.. code-block:: sh

  $ vagrant destroy -f

Try WPS requests
++++++++++++++++

Run a WPS GetCapabilites request::

    $ curl -s -o caps.xml \
      "http://192.168.128.100:5000/wps?service=WPS&request=GetCapabilities"
    $ less caps.xml

Try other OS
++++++++++++

Configure ``Vagrantfile`` with another `Bento Box <https://app.vagrantup.com/bento>`_::

  wps.vm.box = "bento/ubuntu-18.04"

Alternative: use Vagrant without provisioning
---------------------------------------------

Use Vagrant without provisioning and just to setup a new VM::

  $ vagrant destroy -f  # remove previous VM
  $ vagrant up --no-provision  # setup new VM
  $ vagrant ssh  # ssh into VM

Run the installation manually now::

  vagrant> sudo yum install git
  vagrant> cd /vagrant
  vagrant> sudo yum install epel-release
  vagrant> sudo yum install ansible
  vagrant> ln -s etc/sample-vagrant.yml custom.yml
  vagrant> ansible-galaxy install -r requirements.yml
  vagrant> ansible-playbook -c local -i hosts playbook.yml
