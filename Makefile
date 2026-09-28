# Unified entry points for the repository's development procedures.
#
# The real logic lives in tools/native/*.py or tools/*.sh (see INSTRUCTIONS.md);
# these targets are thin wrappers so every procedure has one memorable
# command: `make build`, `make run_tests`, `make generate_screenshots`.

# --- the platform switch --------------------------------------------------
# macOS and Windows default to native host tools. BACKEND=docker opts into
# the pinned Linux image without requiring a host POSIX shell.
ifeq ($(OS),Windows_NT)
HOST := windows
PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python)
else ifeq ($(shell uname -s),Darwin)
HOST := macos
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
else
HOST := linux
endif
BACKEND ?= $(if $(filter linux,$(HOST)),docker,native)
ifeq ($(BACKEND),native)
ifeq ($(HOST),linux)
$(error BACKEND=native is currently supported on Windows and macOS)
endif
DEV := $(PYTHON) tools/native/dev.py
else ifeq ($(BACKEND),docker)
ifeq ($(HOST),linux)
LEG := posix
else
DEV := $(PYTHON) tools/native/docker.py
endif
else
$(error BACKEND must be native or docker)
endif

ARGS ?=
QSB ?=

.PHONY: help all build gates lint run_tests generate_screenshots verify_captures package \
        snapshot_package snapshot_quick format coverage install_hooks dev_up dev_down docker_exec clean generate_shaders \
        test_files dev_install ui_test ui_release_gate

help:
	@echo "Backend: Linux defaults to Docker; macOS/Windows default to native."
	@echo "Set BACKEND=docker on macOS/Windows to use the pinned Linux image."
	@echo "dev_install            native setup; on Linux link checkout into Cura"
	@echo "all                    build, lint, tests, captures, package and snapshot"
	@echo "build                  all gates plus fresh screenshots"
	@echo "gates                  lint, full suite and capture smoke tests"
	@echo "lint                   compile, QML and source/tool checks"
	@echo "run_tests              full test suite, one process per file"
	@echo "test_files             selected files (FILES=\"tests.test_index tests.test_upload\")"
	@echo "generate_screenshots   render captures; Docker refreshes committed copies"
	@echo "verify_captures        verify light/dark capture determinism"
	@echo "generate_shaders       compile packaged Qt shaders"
	@echo "package                build and verify both release artifacts"
	@echo "snapshot_package       copy verified package to the temp directory"
	@echo "snapshot_quick         lint, tests and verified snapshot"
	@echo "format                 format plugin QML"
	@echo "coverage               measured suite and coverage report"
	@echo "install_hooks          install the pre-commit hook"
	@echo "ui_test                real Cura desktop harness (MODE, CURA_VERSION)"
	@echo "ui_release_gate        desktop harness release suite"
	@echo "clean                  remove build outputs and editor backups"
	@echo "dev_up/dev_down         manage a Docker dev container (Docker backend)"
	@echo "docker_exec            run ARGS in the Docker image (Docker backend)"

all: generate_shaders build lint run_tests verify_captures package snapshot_package

build: gates
ifneq ($(LEG),posix)
	$(DEV) refresh
else
	./tools/refresh_screenshots.sh
endif

gates: generate_shaders
ifneq ($(LEG),posix)
	$(DEV) gates
else
	./tools/docker_gates.sh
endif

lint:
ifneq ($(LEG),posix)
	$(DEV) lint
else
	./tools/docker_dev.sh sh -c "python3 -m compileall -q plugins tools tests \
	    && python3 tools/check_qml.py plugins \
	    && python3 tools/check_qml_engine.py \
	    && check_qml_format plugins/*.qml plugins/theme/*.qml \
	    && ruff check plugins tools tests \
	    && shellcheck tools/*.sh \
	    && hadolint Dockerfile \
	    && sh tools/check_workflows.sh \
	    && gitleaks detect --no-git --no-banner --redact"
endif

run_tests:
ifneq ($(LEG),posix)
	$(DEV) test
else
	./tools/run_tests.sh
endif

ifneq ($(LEG),posix)
test_files:
	$(if $(strip $(FILES)),,$(error usage: make test_files FILES="tests.test_monitor_qml_contracts tests.test_index"))
	$(DEV) test $(foreach file,$(FILES),--file $(file))
else
test_files:
	@test -n "$(FILES)" || { \
	    echo 'usage: make test_files FILES="tests.test_monitor_qml_contracts tests.test_index"'; \
	    exit 2; }
	./tools/docker_dev.sh sh -c "cd /work && JOBS=$(or $(JOBS),8) SHARDS=$(or $(SHARDS),1) tools/run_some.sh $(FILES)"
endif

dev_install:
ifneq ($(LEG),posix)
	$(DEV) bootstrap
else
	./tools/install_dev.sh
endif

generate_screenshots:
ifneq ($(LEG),posix)
	$(DEV) refresh
else
	./tools/refresh_screenshots.sh
endif

verify_captures:
ifneq ($(LEG),posix)
	$(DEV) determinism
else
	./tools/verify_capture_determinism.sh
endif

package: generate_shaders
ifneq ($(LEG),posix)
	$(DEV) package
else
	# The two artifacts are independent: build them side by side
	# (the 2026-09-18 parallelism ruling), then verify both.
	python3 tools/build_curapackage.py & python3 tools/build_marketplace_source.py & wait
	python3 tools/verify_curapackage.py "dist/MoonrakerPrintFollower-v$$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])').curapackage"
	python3 tools/verify_marketplace_source.py "dist/MoonrakerPrintFollower-v$$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])')-source.zip"
endif

# The built package is SCP'd to the Cura machine after every push: a
# verified curapackage at a fixed path, rebuilt from the current
# checkout (make package above builds and verifies both artifacts
# first).
snapshot_package: package
ifneq ($(LEG),posix)
	$(DEV) snapshot
else
	cp dist/MoonrakerPrintFollower-v$(shell python3 -c "import json; print(json.load(open('package.json'))['package_version'])").curapackage /tmp/mpf.curapackage
endif

# The FAST iteration path for the snapshot loop (the 2026-09-10
# ruling, amended the same day): lint + the full test suite + a
# verified package, WITHOUT captures and capture determinism â€” the
# snapshot iterations carry logic, so the suites run, and only the
# screenshot machinery is skipped. make all remains mandatory before
# any commit or push.
snapshot_quick:
	$(MAKE) -j2 lint run_tests
ifneq ($(LEG),posix)
	$(DEV) snapshot
else
	$(MAKE) package
	cp dist/MoonrakerPrintFollower-v$(shell python3 -c "import json; print(json.load(open('package.json'))['package_version'])").curapackage /tmp/mpf.curapackage
	@echo "wrote /tmp/mpf.curapackage"
endif

format:
ifneq ($(LEG),posix)
	$(DEV) format
else
	./tools/docker_dev.sh /usr/lib/qt6/bin/qmlformat -i plugins/*.qml plugins/theme/*.qml
endif

coverage:
ifneq ($(LEG),posix)
	$(DEV) coverage
else
	COVERAGE=1 JOBS=$(JOBS) ./tools/run_tests.sh
endif

install_hooks:
ifneq ($(LEG),posix)
	$(DEV) hooks
else
	./tools/install_hooks.sh
endif

ifneq ($(LEG),posix)
dev_up dev_down:
	$(DEV) $@

docker_exec:
	$(DEV) exec $(ARGS)
else
dev_up:
	docker run -d --name mpf-dev --user "$$(id -u):$$(id -g)" -v "$$(git rev-parse --show-toplevel)":/work moonraker-print-follower-dev sleep infinity

dev_down:
	docker rm -f mpf-dev || true

docker_exec:
	./tools/docker_dev.sh $(ARGS)
endif

clean:
ifneq ($(LEG),posix)
	$(DEV) clean
else
	find . -name "__pycache__" -type d -not -path "./.git/*" -prune -exec rm -rf {} +
	find . -name "*~" -not -path "./.git/*" -delete
	rm -rf dist
endif

ifneq ($(LEG),posix)
# The desktop harness is explicit: neither target is part of make all.
ui_test: package
	$(DEV) ui_test --cura-version $(or $(CURA_VERSION),5.13.0) --mode $(or $(MODE),scenario)

ui_release_gate: package
	$(DEV) ui_release_gate
else
ui_test: package  # the harness stages dist â€” it must be the current tree, never a stale build
	./tools/ui_test.sh

# The release gate's real-Cura scenario runs: the gates + suite on the
# primary pinned Cura, the gates again on the secondary version. Run
# on a box with docker; the budgets and retry policy live in
# tools/harness_release.sh (TESTING.md Â§5).
ui_release_gate:
	./tools/harness_release.sh
endif

# Build-time compilation; packaged shader bundles require no user compiler.
generate_shaders:
ifneq ($(LEG),posix)
	$(DEV) shaders $(if $(QSB),--qsb "$(QSB)",)
else
	./tools/docker_dev.sh python3 tools/build_shaders.py $(if $(QSB),--qsb "$(QSB)",)
endif
