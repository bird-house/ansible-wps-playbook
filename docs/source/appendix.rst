Ansible Hints
=============

Show local variables and facts:

.. code-block:: sh

  $ ansible -m setup -c local localhost


Skip specific tasks for quick tests, for example skip ``conda`` tasks:

.. code-block:: sh

  $ ansible-playbook -c local --skip-tags=conda playbook.yml

Links
=====

.. contents::
    :local:
    :depth: 2


Roles/Recipes from Ansible Galaxy
---------------------------------

Used roles:

* miniconda: https://galaxy.ansible.com/andrewrothstein/miniconda/
* nginx: https://galaxy.ansible.com/geerlingguy/nginx
* supervisor: https://galaxy.ansible.com/geerlingguy/supervisor
* postgresql: https://galaxy.ansible.com/anxs/postgresql
* ssl-certs: https://galaxy.ansible.com/jdauphant/ssl-certs
* mongodb: https://galaxy.ansible.com/undergreen/mongodb

Alternative roles:

* postgresql: https://galaxy.ansible.com/geerlingguy/postgresql

Other
-----

* https://tdhopper.com/blog/automating-python-with-ansible/
* https://serversforhackers.com/c/an-ansible-tutorial
* https://plone-ansible-playbook.readthedocs.io/en/latest/index.html
* http://docs.ansible.com/ansible/latest/intro_installation.html
* https://github.com/geocontainers/
* http://pywps.readthedocs.io/en/master/deployment.html#deployment-on-nginx-gunicorn
