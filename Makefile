.DEFAULT_GOAL := all

.PHONY: all
all: help

.PHONY: help
help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  help        to print this help message. (Default)"
	@echo "  roles       to install pinned Galaxy dependencies."
	@echo "  roles-update"
	@echo "              to reinstall edited dependency pins and test."
	@echo "  play        to run ansible playbook."
	@echo "  lint        to lint YAML, Ansible, and test shell scripts."
	@echo "  check       to run an Ansible syntax check."
	@echo "  config-check"
	@echo "              to validate defaults and rendered configuration."
	@echo "  convergence to deploy the tiny test service in AlmaLinux 9 with Docker."
	@echo "  test        to install dependencies and run all local checks."
	@echo "  clean       to remove *all* files that are not controlled by 'git'. WARNING: use it *only* if you know what you do!"

.PHONY: roles
roles:
	@echo "Installing pinned Ansible Galaxy dependencies ..."
	ansible-galaxy role install --force -r requirements.yml
	ansible-galaxy collection install --force -r requirements.yml

.PHONY: roles-update
roles-update: test

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
	git ls-files -z --cached --others --exclude-standard '*.yml' '*.yaml' \
		| xargs -0 bash -c 'files=(); for file; do \
			[[ -f "$$file" ]] && files+=("$$file"); \
		done; (("$${#files[@]}" == 0)) \
			|| yamllint --config-file .yamllint.yml "$${files[@]}"' _
	ansible-lint playbook.yml tests/configuration.yml roles/common roles/pywps roles/roocs roles/slurm roles/supervisor
	bash -n tests/convergence/run.sh

.PHONY: test
test: roles lint check config-check

.PHONY: config-check
config-check:
	ansible-playbook -i localhost, tests/configuration.yml

.PHONY: convergence
convergence:
	tests/convergence/run.sh

.PHONY: clean
clean:
	@echo "Cleaning ..."
	@if ! git diff --quiet HEAD; then \
		echo "There are uncommitted changes! Not running 'git clean'."; \
		exit 1; \
	fi
	@git clean -dfx -e *.bak -e custom.yml -e etc/custom*.yml -e .vagrant
