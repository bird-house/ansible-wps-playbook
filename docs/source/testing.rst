Testing
=======

.. contents::
    :local:
    :depth: 2

.. _docker:

Test Ansible in a Docker container
----------------------------------

Start an Ubuntu Docker container and mount local source::

    $ ./run_docker.sh

Run the Ansible deployment::

    $ ./bootstrap.sh
    $ make play

Check if application is started (supervisor)::

    $ supervisorctl status

Check also nginx ... might not start automatically in docker::

     $ service nginx status
     $ service nginx start # if not already started

Run a WPS GetCapabilites request::

    $ curl -s -o caps.xml \
      "http://127.0.0.1:5000/wps?service=WPS&request=GetCapabilities"
    $ less caps.xml

Check log files::

    $ supervisorctl tail -f emu

Try more WPS requests::

    # show description of "hello" process
    $ curl -s -o out.xml \
      "http://127.0.0.1:5000/wps?service=WPS&request=DescribeProcess&version=1.0.0&identifier=hello"
    $ less out.xml

    # execute "hello" process
    $ curl -s -o out.xml \
      "http://127.0.0.1:5000/wps?service=WPS&request=Execute&version=1.0.0&identifier=hello&DataInputs=name=Spaetzle"
    § less out.xml

Test Ansible with Vagrant
-------------------------

Get Ready
+++++++++

You need to install Vagrant. See the following links for details:

* https://docs.ansible.com/ansible/latest/scenario_guides/guide_vagrant.html
* https://www.vagrantup.com/intro/getting-started/index.html
* https://blog.scriptmyjob.com/creating-an-ansible-testing-environment-using-vagrant-on-macos/

In short, you can install Vagrant on macOS with `Homebrew <https://brew.sh/>`_
(and `Homebrew Cask <https://caskroom.github.io/>`_)::

  $ brew cask install virtualbox
  $ brew cask install vagrant

You need Ansible locally installed::

  $ ./bootstrap.sh  # Linux
  OR
  $ brew install ansible # macOS

Install Ansible roles::

  $ ansible-galaxy install -p roles -r requirements.yml --ignore-errors

Run Vagrant
+++++++++++

Initial setup::

  $ vagrant up

Provision with ansible again::

  $ vagrant provision

Login with SSH::

  $ vagrant ssh

Run Ansible manually::

  $ ansible-playbook -i .vagrant/provisioners/ansible/inventory/vagrant_ansible_inventory playbook.yml

Remove VMs::

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
