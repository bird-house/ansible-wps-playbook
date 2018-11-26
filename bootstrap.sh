#!/bin/bash

upgrade_redhat_6() {
  echo "Upgrade Ansbile on RedHat 6"
  sudo yum install python27
  curl "https://bootstrap.pypa.io/get-pip.py" -o "/tmp/get-pip.py"
  sudo python2.7 /tmp/get-pip.py
  sudo pip install ansible
}

bootstrap() {
    echo "Bootstrap Ansible ..."

    if [[ $EUID -eq 0 ]]; then
        echo "Enable sudo ..."
        if [ -f /etc/debian_version ] ; then
            apt-get update -y && apt-get install -y sudo
        elif [ -f /etc/redhat-release ] ; then
            yum update -y && yum install -y sudo
        fi
    fi

    if [ -f /etc/debian_version ] ; then
        echo "Install Debian/Ubuntu packages ..."
        sudo apt-get update -y
        sudo apt-get install -y software-properties-common build-essential
        # install ansible
        sudo apt-add-repository -y ppa:ansible/ansible
        sudo apt-get update -y
        sudo apt-get install -y ansible
        # sudo apt-get install -y vim-common # anaconda needs xxd
    elif [ -f /etc/redhat-release ] ; then
        echo "Install RedHat/CentOS packages ..."
        sudo yum update -y
        sudo yum install -y epel-release
        sudo yum install -y gcc-c++ make
        sudo yum install -y ansible
        major_release=$(cat /etc/redhat-release | tr -dc '0-9.'|cut -d \. -f1)
        if [ $major_release = "6" ] ; then
          if [ -f /etc/centos-release ] ; then
            # needed for galaxy usage with python 2.6 on CentOS 6
            sudo yum install -y python-urllib3 pyOpenSSL python2-ndg_httpsclient python-pyasn1
          else
            upgrade_redhat_6
          fi
        fi
    elif [ `uname -s` = "Darwin" ] ; then
        echo "Install Homebrew packages ..."
        brew install ansible
    fi
}


bootstrap
