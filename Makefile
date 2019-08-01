.DEFAULT_GOAL := all

.PHONY: all
all: help

.PHONY: help
help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  help        to print this help message. (Default)"
	@echo "  bootstrap   to boostrap Ansible installation."
	@echo "  roles       to install roles from ansible-galaxy."
	@echo "  play        to run ansible playbook."
	@echo "  clean       to remove *all* files that are not controlled by 'git'. WARNING: use it *only* if you know what you do!"

.PHONY: bootstrap
bootstrap:
	@echo "Bootstrap Ansible ..."
	@bash bootstrap.sh

.PHONY: roles
roles:
	@echo "Installing required Ansible roles from ansible-galaxy ..."
	ansible-galaxy install -p roles -r requirements.yml

.PHONY: quick
quick: roles
	echo "Installing PyWPS application with Ansible [skip conda tasks] ..."
	ansible-playbook -c local --skip conda playbook.yml

.PHONY: play
play: roles
	echo "Installing PyWPS application with Ansible [all tasks] ..."
	ansible-playbook -c local playbook.yml

.PHONY: clean
clean:
	@echo "Cleaning ..."
	@git diff --quiet HEAD || echo "There are uncommited changes! Not doing 'git clean' ..."
	@-git clean -dfx -e *.bak -e custom.yml -e etc/custom*.yml
