# Unified entry points for the repository's development procedures.
#
# The real logic lives in the tools/*.sh scripts (see INSTRUCTIONS.md);
# these targets are thin wrappers so every procedure has one memorable
# command: `make build`, `make run_tests`, `make generate_screenshots`.

ARGS ?=

.PHONY: help all build gates lint run_tests generate_screenshots verify_captures package \
        snapshot_package snapshot_quick format coverage install_hooks dev_up dev_down docker_exec clean

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
	@echo "verify_captures        two capture runs in the pinned container must be"
	@echo "                       byte-identical (catches leaked live inputs)"
	@echo "snapshot_package       build + verify, then copy the curapackage to"
	@echo "                       /tmp/mpf.curapackage for the author to SCP"
	@echo "snapshot_quick         the FAST iteration path: lint + run_tests + a"
	@echo "                       verified package, no captures or determinism"
	@echo "package                build and verify the Cura package and Marketplace ZIP"
	@echo "format                 apply qmlformat to the plugin QML (in the container)"
	@echo "coverage               coverage run and report for plugins/ (in the container)"
	@echo "install_hooks          install the pre-commit hook"
	@echo "dev_up                 start the warm dev container (docker_dev.sh"
	@echo "                       then reuses it; recreated after any rebuild)"
	@echo "dev_down               stop the warm dev container"
	@echo "docker_exec            run a command in the dev container"
	@echo "                       (make docker_exec ARGS=\"qmlformat -i plugins/X.qml\")"
	@echo "clean                  remove build outputs and editor backups"

all: build lint run_tests verify_captures package snapshot_package

build: gates
	./tools/refresh_screenshots.sh --copy-only

gates:
	./tools/docker_gates.sh

lint:
	./tools/docker_dev.sh sh -c "python3 -m compileall -q plugins tools tests \
	    && python3 tools/check_qml.py plugins \
	    && python3 tools/check_qml_engine.py \
	    && check_qml_format plugins/*.qml \
	    && ruff check plugins tools tests \
	    && shellcheck tools/*.sh \
	    && hadolint Dockerfile \
	    && gitleaks detect --no-git --no-banner --redact"

run_tests:
	./tools/run_tests.sh

dev_install:
	./tools/install_dev.sh

generate_screenshots:
	./tools/refresh_screenshots.sh

verify_captures:
	./tools/verify_capture_determinism.sh

package:
	python3 tools/build_curapackage.py
	python3 tools/build_marketplace_source.py
	python3 tools/verify_curapackage.py "dist/MoonrakerPrintFollower-v$$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])').curapackage"
	python3 tools/verify_marketplace_source.py "dist/MoonrakerPrintFollower-v$$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])')-source.zip"

# The author SCPs the built package to the Cura machine after every
# push: a verified curapackage at a fixed path, rebuilt from the
# current checkout (make package above builds and verifies both
# artifacts first).
snapshot_package: package
	cp dist/MoonrakerPrintFollower-v$(shell python3 -c "import json; print(json.load(open('package.json'))['package_version'])").curapackage /tmp/mpf.curapackage

# The FAST iteration path for the snapshot loop (the author's ruling,
# 2026-09-10, amended the same day): lint + the full test suite + a
# verified package, WITHOUT captures and capture determinism — the
# snapshot iterations carry logic, so the suites run, and only the
# screenshot machinery is skipped. make all remains mandatory before
# any commit or push.
snapshot_quick: lint run_tests
	$(MAKE) package
	cp dist/MoonrakerPrintFollower-v$(shell python3 -c "import json; print(json.load(open('package.json'))['package_version'])").curapackage /tmp/mpf.curapackage
	@echo "wrote /tmp/mpf.curapackage"

format:
	./tools/docker_dev.sh /usr/lib/qt6/bin/qmlformat -i plugins/*.qml

coverage:
	./tools/docker_dev.sh sh -c "coverage run -m unittest discover -s tests -p 'test_*.py' \
	    && coverage report --include='plugins/*' --fail-under=80"

install_hooks:
	./tools/install_hooks.sh

dev_up:
	docker run -d --name mpf-dev --user "$$(id -u):$$(id -g)" -v "$$(git rev-parse --show-toplevel)":/work moonraker-print-follower-dev sleep infinity

dev_down:
	docker rm -f mpf-dev || true

docker_exec:
	./tools/docker_dev.sh $(ARGS)

clean:
	find . -name "__pycache__" -type d -not -path "./.git/*" -prune -exec rm -rf {} +
	find . -name "*~" -not -path "./.git/*" -delete
	rm -rf dist

ui_test:
	./tools/ui_test.sh

# The release gate's real-Cura scenario runs: the gates + suite on the
# primary pinned Cura, the gates again on the secondary version. Run
# on a box with docker; the budgets and retry policy live in
# tools/harness_release.sh (TESTING.md §5).
ui_release_gate:
	./tools/harness_release.sh
