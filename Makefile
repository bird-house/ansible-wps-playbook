.DEFAULT_GOAL := all

.PHONY: all
all: help

.PHONY: help
help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  help        to print this help message. (Default)"
	@echo "  roles       to install roles from ansible-galaxy."
	@echo "  play        to run ansible playbook."
	@echo "  lint        to lint tracked YAML and Ansible files."
	@echo "  check       to run an Ansible syntax check."
	@echo "  smoke       to validate defaults without changing the host."
	@echo "  test        to install dependencies and run all local checks."
	@echo "  clean       to remove *all* files that are not controlled by 'git'. WARNING: use it *only* if you know what you do!"

.PHONY: roles
roles:
	@echo "Installing required Ansible roles and collections from ansible-galaxy ..."
	ansible-galaxy install -r requirements.yml
	ansible-galaxy collection install -r requirements.yml

.PHONY: quick
quick: roles
	echo "Installing PyWPS application with Ansible [skip conda tasks] ..."
	ansible-playbook -c local --skip-tags conda -i hosts playbook.yml

.PHONY: play
play: roles
	echo "Installing PyWPS application with Ansible [all tasks] ..."
	ansible-playbook -c local -i hosts playbook.yml --extra-vars "@extra_vars.yml"

.PHONY: check
check:
	ansible-playbook --syntax-check -i hosts playbook.yml

.PHONY: lint
lint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 yamllint --config-file .yamllint.yml
	ansible-lint playbook.yml tests/smoke.yml roles/common roles/pywps roles/roocs roles/slurm roles/supervisor

.PHONY: test
test: roles lint check smoke

.PHONY: smoke
smoke:
	ansible-playbook -i localhost, tests/smoke.yml

.PHONY: clean
clean:
	@echo "Cleaning ..."
	@if ! git diff --quiet HEAD; then \
		echo "There are uncommitted changes! Not running 'git clean'."; \
		exit 1; \
	fi
	@git clean -dfx -e *.bak -e custom.yml -e etc/custom*.yml -e .vagrant
