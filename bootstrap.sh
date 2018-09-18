#!/bin/bash

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
        echo "Install CentOS packages ..."
        sudo yum update -y
        sudo yum install -y epel-release
        sudo yum install -y gcc-c++ make
        sudo yum install -y ansible
        if [ `grep -oE '[0-9]+\.' /etc/redhat-release` = "6." ] ; then
          # needed for galaxy usage with python 2.6 on CentOS 6
          sudo yum install -y python-urllib3 pyOpenSSL python2-ndg_httpsclient python-pyasn1
        fi
    elif [ `uname -s` = "Darwin" ] ; then
        echo "Install Homebrew packages ..."
        brew install ansible
    fi
}


bootstrap
