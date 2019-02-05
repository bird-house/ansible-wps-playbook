Changes
*******

0.2.1 (2019-02-05)
==================

Changes:

* Configure wps user with optional UID/GID (#56).
* Support for load-balancing configuration (#68).
* Added a flag `wps_add_user` to skip task "wps add user" (#64, #66).
* Using `extra_config` to extend the pywps configuration (#60, #62).
* Updated docs (#59).
* Several bug-fixes (#61, #65)

0.2.0 (2018-12-06)
==================

Washington Release.

Changes:

* Fixed RedHad/CentOS 6 issues (#50, #49).
* Fixed CentOS 7 issue (#46).
* Support HTTPS (#30, #45).
* Fixed firewall issue (#39).
* Support output file-service used by multiple WPS (#37).

0.1.1 (2018-09-19)
==================

Changes:

* Updated to latest version 2.0.2 of supervisor role (#31).
* Added support for CentOS 6.x (#34).
* PyWPS `outputurl` parameter is now configurable (#36).

0.1.0 (2018-09-05)
==================

This is the first release of the Ansible playbook for PyWPS.

Features:

* Install PyWPS application with Nginx, Supervisor, Gunicorn and PostgreSQL.
* Configuration options can be overwritten using a ``custom.yml`` file.
* Allows the installation of multiple PyWPS applications.
* PostgreSQL installation is optional.
