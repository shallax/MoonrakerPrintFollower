# Reproducible development/test environment for MoonrakerPrintFollower.
#
# The image carries the toolchain only; the repository is mounted at
# run time, so builds stay fast and iterate on the live checkout:
#
#   docker build -t moonraker-print-follower-dev .
#   docker run --rm -v "$PWD":/work moonraker-print-follower-dev
#
# The default command runs every gate exactly as the CI lint and test
# jobs do (compile, QML structure, qmlformat, ruff, unit tests with the
# real Qt runtime). Override CMD to run a subset, e.g.:
#
#   docker run --rm -v "$PWD":/work moonraker-print-follower-dev \
#       sh -c "python3 -m unittest discover -s tests -p 'test_*.py'"
FROM ubuntu:26.04

# Exact versions from the ubuntu:26.04 archive (bump together when the
# base image moves).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git=1:2.53.0-1ubuntu1 \
        python3=3.14.3-0ubuntu2 \
        python3-pip=25.1.1+dfsg-1ubuntu2 \
        python3-venv=3.14.3-0ubuntu2 \
        wget=1.25.0-2ubuntu4.4 \
        fonts-dejavu-core=2.37-8build1 \
        libegl1=1.7.0-3 \
        libgl1=1.7.0-3 \
        libxkbcommon0=1.13.1-1 \
        qt6-declarative-dev-tools=6.10.2+dfsg-3 \
        shellcheck=0.11.0-2 \
    && rm -rf /var/lib/apt/lists/* \
    && case "$(uname -m)" in aarch64) lint_arch=arm64 ;; *) lint_arch=x86_64 ;; esac \
    && wget -qO /usr/local/bin/hadolint \
        "https://github.com/hadolint/hadolint/releases/download/v2.12.0/hadolint-Linux-${lint_arch}" \
    && chmod +x /usr/local/bin/hadolint \
    && wget -qO /tmp/gitleaks.tar.gz \
        https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
    && tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks \
    && rm /tmp/gitleaks.tar.gz

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir PyQt6==6.11.0 PyQt6-Qt6==6.11.2 ruff==0.16.6 coverage numpy

ENV PATH="/opt/venv/bin:$PATH" \
    QT_QPA_PLATFORM=offscreen

# The bind-mounted checkout is owned by the host user; teach git to
# trust it system-wide (the container usually runs as --user
# "$(id -u):$(id -g)", so a root-home global config would not apply).
RUN git config --system --add safe.directory /work

WORKDIR /work

# The wrapper uses the format-to-stdout diff path (equivalent behaviour
# across Qt versions). The binary installs outside PATH
# (/usr/lib/qt6/bin); the symlink keeps plain `qmlformat` invocations
# working for editors and quick iteration (by request).
COPY tools/check_qml_format.sh /usr/local/bin/check_qml_format
RUN chmod +x /usr/local/bin/check_qml_format \
    && ln -s /usr/lib/qt6/bin/qmlformat /usr/local/bin/qmlformat

CMD ["sh", "-c", "python3 -m compileall -q plugins tools tests \
    && python3 tools/check_qml.py plugins \
    && check_qml_format plugins/*.qml \
    && ruff check plugins tools tests \
    && shellcheck tools/*.sh \
    && hadolint Dockerfile \
    && gitleaks detect --no-git --no-banner --redact \
    && coverage run -m unittest discover -s tests -p 'test_*.py' \
    && coverage report --include='plugins/*' --fail-under=80 \
    && python3 tools/capture_monitor.py dist/screenshots \
    && python3 tools/capture_preview.py dist/screenshots \
    && python3 tools/capture_settings.py dist/screenshots \
    && python3 tools/capture_upload.py dist/screenshots \
    && python3 tools/capture_whatsnew.py dist/screenshots"]
