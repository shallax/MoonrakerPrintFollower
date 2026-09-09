# Unified entry points for the repository's development procedures.
#
# The real logic lives in the tools/*.sh scripts (see INSTRUCTIONS.md);
# these targets are thin wrappers so every procedure has one memorable
# command: `make build`, `make run_tests`, `make generate_screenshots`.

ARGS ?=

.PHONY: help all build gates lint run_tests generate_screenshots package \
        format coverage install_hooks docker_exec clean

help:
	@echo "all                    everything: gates, tests, screenshots, package"
	@echo "build                  full verification build: every gate plus fresh"
	@echo "                       committed screenshots (what CI checks)"
	@echo "gates                  all checks and captures inside the pinned dev"
	@echo "                       container"
	@echo "lint                   the linting and structure checks only, in the"
	@echo "                       pinned container (no tests, no captures)"
	@echo "run_tests              the full test suite: stdlib on the host, then"
	@echo "                       the real-Qt suite in the container"
	@echo "generate_screenshots   regenerate the canonical captures in the"
	@echo "                       container and refresh the committed copies"
	@echo "package                build and verify the Cura package and Marketplace ZIP"
	@echo "format                 apply qmlformat to the plugin QML (in the container)"
	@echo "coverage               coverage run and report for plugins/ (in the container)"
	@echo "install_hooks          install the pre-commit hook"
	@echo "docker_exec            run a command in the dev container"
	@echo "                       (make docker_exec ARGS=\"qmlformat -i plugins/X.qml\")"
	@echo "clean                  remove build outputs and editor backups"

all: build lint run_tests package

build: gates
	./tools/refresh_screenshots.sh --copy-only

gates:
	./tools/docker_gates.sh

lint:
	./tools/docker_dev.sh sh -c "python3 -m compileall -q plugins tools tests \
	    && python3 tools/check_qml.py plugins \
	    && check_qml_format plugins/*.qml \
	    && ruff check plugins tools tests \
	    && shellcheck tools/*.sh \
	    && hadolint Dockerfile \
	    && gitleaks detect --no-git --no-banner --redact"

run_tests:
	./tools/run_tests.sh

generate_screenshots:
	./tools/refresh_screenshots.sh

package:
	python3 tools/build_curapackage.py
	python3 tools/build_marketplace_source.py
	python3 tools/verify_curapackage.py "dist/MoonrakerPrintFollower-v$$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])').curapackage"
	python3 tools/verify_marketplace_source.py "dist/MoonrakerPrintFollower-v$$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])')-source.zip"

format:
	./tools/docker_dev.sh /usr/lib/qt6/bin/qmlformat -i plugins/*.qml

coverage:
	./tools/docker_dev.sh sh -c "coverage run -m unittest discover -s tests -p 'test_*.py' \
	    && coverage report --include='plugins/*' --fail-under=80"

install_hooks:
	./tools/install_hooks.sh

docker_exec:
	./tools/docker_dev.sh $(ARGS)

clean:
	find . -name "__pycache__" -type d -not -path "./.git/*" -prune -exec rm -rf {} +
	find . -name "*~" -not -path "./.git/*" -delete
	rm -rf dist
