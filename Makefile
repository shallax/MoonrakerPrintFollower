# Unified entry points for the repository's development procedures.
#
# The real logic lives in the tools/*.sh scripts (see INSTRUCTIONS.md);
# these targets are thin wrappers so every procedure has one memorable
# command: `make build`, `make run_tests`, `make generate_screenshots`.

# --- the platform switch --------------------------------------------------
# POSIX hosts run the procedures as tools/*.sh, the container legs
# included. Windows has no POSIX shell on PATH and no pinned container,
# so the same procedures have a native implementation under
# tools/windows (INSTRUCTIONS.md, "Windows development"): every target
# keeps its name, its arguments and its meaning on both legs.
ifeq ($(OS),Windows_NT)
LEG := windows
# The venv `make dev_install` creates; a bare python is the fallback
# before it has run.
PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python)
DEV := $(PYTHON) tools/windows/dev.py
else
LEG := posix
endif

ARGS ?=
QSB ?=

.PHONY: help all build gates lint run_tests generate_screenshots verify_captures package \
        snapshot_package snapshot_quick format coverage install_hooks dev_up dev_down docker_exec clean generate_shaders

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
	@echo "test_files             a CHOSEN list of test files, one process per"
	@echo "                       file, run in PARALLEL in the container â€” use"
	@echo "                       this instead of one unittest with many files,"
	@echo "                       which runs them serially (the Qt files also"
	@echo "                       need their own process)"
	@echo "                       (make test_files FILES=\"tests.test_monitor_qml_contracts tests.test_index\")"
	@echo "generate_screenshots   regenerate the canonical captures in the"
	@echo "                       container and refresh the committed copies"
	@echo "verify_captures        two capture runs in the pinned container must be"
	@echo "                       byte-identical (catches leaked live inputs)"
	@echo "snapshot_package       build + verify, then copy the curapackage to"
	@echo "                       /tmp/mpf.curapackage, ready to SCP"
	@echo "snapshot_quick         the FAST iteration path: lint + run_tests + a"
	@echo "                       verified package, no captures or determinism"
	@echo "package                build and verify the Cura package and Marketplace ZIP"
	@echo "format                 apply qmlformat to the plugin QML (in the container)"
	@echo "generate_shaders       compile the packaged GPU shaders (also part of all)"
	@echo "coverage               coverage run and report for plugins/ (in the container)"
	@echo "install_hooks          install the pre-commit hook"
	@echo "dev_up                 start the warm dev container (docker_dev.sh"
	@echo "                       then reuses it; recreated after any rebuild)"
	@echo "dev_down               stop the warm dev container"
	@echo "docker_exec            run a command in the dev container"
	@echo "                       (make docker_exec ARGS=\"qmlformat -i plugins/X.qml\")"
	@echo "clean                  remove build outputs and editor backups"

all: generate_shaders build lint run_tests verify_captures package snapshot_package

build: gates
ifeq ($(LEG),windows)
	@echo "build: captures are fresh under dist/screenshots; the committed copies"
	@echo "       are canonical only from the pinned container, so they are not"
	@echo "       refreshed here (INSTRUCTIONS.md, Windows development)."
else
	./tools/refresh_screenshots.sh
endif

gates: generate_shaders
ifeq ($(LEG),windows)
	$(DEV) gates
else
	./tools/docker_gates.sh
endif

lint:
ifeq ($(LEG),windows)
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
ifeq ($(LEG),windows)
	$(DEV) test
else
	./tools/run_tests.sh
endif

ifeq ($(LEG),windows)
test_files:
	@if not defined FILES (echo usage: make test_files FILES="tests.test_monitor_qml_contracts tests.test_index" & exit /b 2)
	$(DEV) test $(foreach file,$(FILES),--file $(file))
else
test_files:
	@test -n "$(FILES)" || { \
	    echo 'usage: make test_files FILES="tests.test_monitor_qml_contracts tests.test_index"'; \
	    exit 2; }
	./tools/docker_dev.sh sh -c "cd /work && JOBS=$(or $(JOBS),8) SHARDS=$(or $(SHARDS),1) tools/run_some.sh $(FILES)"
endif

dev_install:
ifeq ($(LEG),windows)
	$(DEV) bootstrap
else
	./tools/install_dev.sh
endif

generate_screenshots:
ifeq ($(LEG),windows)
	$(DEV) captures
	@echo "generate_screenshots: dist/screenshots only - the committed copies"
	@echo "       come from the pinned container (INSTRUCTIONS.md)."
else
	./tools/refresh_screenshots.sh
endif

verify_captures:
ifeq ($(LEG),windows)
	$(DEV) determinism
else
	./tools/verify_capture_determinism.sh
endif

package: generate_shaders
ifeq ($(LEG),windows)
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
ifeq ($(LEG),windows)
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
ifeq ($(LEG),windows)
	$(DEV) snapshot
else
	$(MAKE) package
	cp dist/MoonrakerPrintFollower-v$(shell python3 -c "import json; print(json.load(open('package.json'))['package_version'])").curapackage /tmp/mpf.curapackage
	@echo "wrote /tmp/mpf.curapackage"
endif

format:
ifeq ($(LEG),windows)
	$(DEV) format
else
	./tools/docker_dev.sh /usr/lib/qt6/bin/qmlformat -i plugins/*.qml plugins/theme/*.qml
endif

coverage:
ifeq ($(LEG),windows)
	$(DEV) coverage
else
	COVERAGE=1 JOBS=$(JOBS) ./tools/run_tests.sh
endif

install_hooks:
ifeq ($(LEG),windows)
	$(DEV) hooks
else
	./tools/install_hooks.sh
endif

ifeq ($(LEG),windows)
# Docker is not part of the Windows leg: the procedures run natively.
dev_up dev_down docker_exec:
	@echo "$@: the Windows leg runs natively - there is no container to manage."
	@echo "       Use make lint / run_tests / test_files / gates / dev_install."
	@exit /b 1
else
dev_up:
	docker run -d --name mpf-dev --user "$$(id -u):$$(id -g)" -v "$$(git rev-parse --show-toplevel)":/work moonraker-print-follower-dev sleep infinity

dev_down:
	docker rm -f mpf-dev || true

docker_exec:
	./tools/docker_dev.sh $(ARGS)
endif

clean:
ifeq ($(LEG),windows)
	$(DEV) clean
else
	find . -name "__pycache__" -type d -not -path "./.git/*" -prune -exec rm -rf {} +
	find . -name "*~" -not -path "./.git/*" -delete
	rm -rf dist
endif

ifeq ($(LEG),windows)
# The real-Cura UI harness has a Windows leg of its own already
# (tools/native_harness.ps1 stages Cura, the plugin and the driver;
# tests/harness/runner.py drives them). It needs a desktop session and
# touches a Cura install, so it is NOT wired into a recipe that a
# stray `make all` could reach â€” the sequence is in INSTRUCTIONS.md,
# "Windows development".
ui_test ui_release_gate:
	@echo "$@: no container on this leg â€” run the native harness instead:"
	@echo "  powershell -ExecutionPolicy Bypass -File tools/native_harness.ps1 -CuraVersion 5.13.0 -Scenario suite"
	@echo "  then drive tests/harness/runner.py with the printed harness_env.ps1"
	@exit /b 1
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
ifeq ($(LEG),windows)
	$(PYTHON) tools/build_shaders.py $(if $(QSB),--qsb "$(QSB)",)
else
	./tools/docker_dev.sh python3 tools/build_shaders.py $(if $(QSB),--qsb "$(QSB)",)
endif
