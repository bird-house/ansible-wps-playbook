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
