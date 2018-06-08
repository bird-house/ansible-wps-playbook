# Ansible Role tests

To run the tests:

  1. Install and start Docker.
  ---
  # 1. Download the test shim (see .travis.yml file for the URL) into `tests/test.sh`:
  #    - `wget -O tests/test.sh https://gist.githubusercontent.com/geerlingguy/73ef1e5ee45d8694570f334be385e181/raw/`
  ---
  1. Make the test shim executable: `chmod +x tests/test_playbook.sh`.
  1. Run (from the role root directory) `distro=[distro] ./tests/test_playbook.sh`

If you don't want the container to be automatically deleted after the test playbook is run, add the following environment variables: `cleanup=false container_id=$(date +%s)`

## Links

* https://github.com/geerlingguy/ansible-role-supervisor/tree/master/tests
