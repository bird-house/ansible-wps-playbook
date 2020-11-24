.DEFAULT_GOAL := all

.PHONY: all
all: help

.PHONY: help
help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  help        to print this help message. (Default)"
	@echo "  roles       to install roles from ansible-galaxy."
	@echo "  play        to run ansible playbook."
	@echo "  clean       to remove *all* files that are not controlled by 'git'. WARNING: use it *only* if you know what you do!"

.PHONY: roles
roles:
	@echo "Installing required Ansible roles and collections from ansible-galaxy ..."
	ansible-galaxy install -r requirements.yml
	ansible-galaxy collection install -r requirements.yml

.PHONY: quick
quick: roles
	echo "Installing PyWPS application with Ansible [skip conda tasks] ..."
	ansible-playbook -c local --skip conda -i hosts playbook.yml

.PHONY: play
play: roles
	echo "Installing PyWPS application with Ansible [all tasks] ..."
	ansible-playbook -c local -i hosts playbook.yml

.PHONY: clean
clean:
	@echo "Cleaning ..."
	@git diff --quiet HEAD || echo "There are uncommited changes! Not doing 'git clean' ..."
	@-git clean -dfx -e *.bak -e custom.yml -e etc/custom*.yml -e .vagrant
