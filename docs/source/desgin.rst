Ideas
======

.. contents::
    :local:
    :depth: 2

Deployment Scenarios
--------------------

A PyWPS service may be used in the following deployment scenarios:

* testing and demo
  - might not have admin rights
  - nginx, supervisor and even gunicorn not necessary ... werkzeug/pywps has its own wsgi service which can be used for development.
  - support by ansible and buildout not necessary
  - just clone repo and setup conda environment
  - should just run with defaults ... no further configuration necessary.
* development on local laptop
  - no full installation necessary (skip Nginx, Supervisor, ...)
  - might not have admin rights
  - enabled debug mode
  - could be the same as the testing/demo variant.
* production ready installation on a single host
  - currently the default
  - can also be used as development environment.
* production installation on a cluster system
  - needs installation of slurm/grid-engine
  - see ansible slurm/grid-engine examples:
    https://github.com/bird-house/birdhouse-ansible
* docker container for testing and demo
  - We currently have a single container with PyWPS, Nginx, Supervisor
  - Container orchestration people don't like this :)
  - Wanted: micro-service + docker-compose
  - Quick-fix: just update the Dockerfile template and extend docker-compose configuration.
* docker container for orchestration
  - Kubernetes seems to be the favorite orchestration tool by admins.
  - Docker Swarm looks easier ... might be used for testing. But Docker support for Kubernetes is evolving.
  - Wanted: micro-service, a single PyWPS service without Nginx and Supervisor.

Food for Thought
----------------

* Ansible and Buildout are not used for the same purpose ... there is a philosophy conflict. Ansible is on the system level (but it could just be localhost), Buildout is on the application level (localhost only). In Ansible examples packages (like Nginx, Supervisor, ...) are installed on the system (Debian, CentOS). In the current Birdhouse deployment solution with Buildout all packages and configs (besides Makefile, gcc, ...) are installed in the user space ... no admin rights are necessary and full installation can be wiped out easily. Probably need to combine best of both sides depending on the deployment scenario.
* Just a single Ansible deployment with configs for all birds? Or a minimal Ansible config in each bird repo fetching roles/recipes from ansible-galaxy?
* A PyWPS service can be run without the need of Ansible and Buildout ... just using a Werkzeug WSGI service and a minimal default configuration. Can be used for testing, demo and development. Need to figure out if current developers will like it :)
* We need to provide a PyWPS micro-service docker container. This should simplify the Docker installation drastically ... might be just a simple Dockerfile template. Complexity will be moved to docker-compose to wire micro-services to serve as a single web application. The other micro-services should be official images on docker-cloud.
* Ansible is currently installed via system packages. But it could also be installed via Conda. That would mean Conda needs to be installed first (bootstrap). But Conda can be installed and updated via Ansible (miniconda role from ansible-galaxy).
