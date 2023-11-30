Changes
*******

0.5.0 (2023-11-30)
==================

Changes:

* updates
* support for site specific roocs configs (#122) 
* added nginx access control (#127)
* added smoke test runner (#132)
* added config for gunicorn (#136)
* added support for flamingo WPS (#140, #141)
* added logrotate for slurm (#143)

0.4.0 (2020-09-22)
==================

Changes:

* added cleantempdir option (#107).
* skip epel setup when not used (#106).
* added demo mode for test data (#105).
* fixed local deployment (#103).
* added clean task (#102).
* added support for slurm cluster deployment (#99, #100, #101).
* use pip install for extra packages (#97, #98).

0.3.0 (2020-01-20)
==================

Changes:

* Skipped Twitcher role (#91)

0.2.3 (2020-01-08)
==================

Changes:

* Added Keycloak support for Twitcher (#87).
* Fixed SSL client verification (#86).
* Fixed postgres user config (#85).
* Don't pin roles version (#84).

0.2.2 (2019-09-27)
==================

Bucharest Release.

Changes:

* Initial twitcher support (#82, #76).
* Updated docs for DB config (#79).
* Support conda spec (#74).
* Fixes (#80, #81).

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
