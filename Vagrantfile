# -*- mode: ruby -*-
# vi: set ft=ruby :

Vagrant.require_version ">= 2.4.0"

Vagrant.configure("2") do |config|
  config.vm.box = ENV.fetch("VAGRANT_BOX", "bento/almalinux-9")
  config.vm.hostname = "wps.local"
  config.vm.network "private_network", ip: "192.168.128.100"

  config.vm.provider "virtualbox" do |virtualbox|
    virtualbox.cpus = ENV.fetch("VAGRANT_CPUS", "2").to_i
    virtualbox.memory = ENV.fetch("VAGRANT_MEMORY", "4096").to_i
  end

  config.vm.provision "shell", name: "Install Ansible test tools", inline: <<~SHELL
    dnf install --assumeyes git make python3.11 python3.11-pip
    python3.11 -m pip install \
      --disable-pip-version-check \
      ansible-core==2.19.11
  SHELL
end
