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
FROM ubuntu:24.04

# Exact versions from the ubuntu:24.04 archive (bump together when the
# base image moves).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git=1:2.43.0-1ubuntu7.3 \
        python3=3.12.3-0ubuntu2.1 \
        python3-pip=24.0+dfsg-1ubuntu1.3 \
        python3-venv=3.12.3-0ubuntu2.1 \
        wget=1.21.4-1ubuntu4.5 \
        fonts-dejavu-core=2.37-8 \
        libegl1=1.7.0-1build1 \
        libgl1=1.7.0-1build1 \
        libxkbcommon0=1.6.0-1build1 \
        qt6-declarative-dev-tools=6.4.2+dfsg-4build3 \
        shellcheck=0.9.0-1 \
    && rm -rf /var/lib/apt/lists/* \
    && wget -qO /usr/local/bin/hadolint \
        https://github.com/hadolint/hadolint/releases/download/v2.12.0/hadolint-Linux-x86_64 \
    && chmod +x /usr/local/bin/hadolint \
    && wget -qO /tmp/gitleaks.tar.gz \
        https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
    && tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks \
    && rm /tmp/gitleaks.tar.gz

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir PyQt6==6.11.0 PyQt6-Qt6==6.11.2 ruff==0.16.6 coverage

ENV PATH="/opt/venv/bin:$PATH" \
    QT_QPA_PLATFORM=offscreen

# The bind-mounted checkout is owned by the host user; teach git to
# trust it system-wide (the container usually runs as --user
# "$(id -u):$(id -g)", so a root-home global config would not apply).
RUN git config --system --add safe.directory /work

WORKDIR /work

# qmlformat 6.4 (Ubuntu 24.04) predates --check: format to stdout and
# diff against the file instead. The binary installs outside PATH
# (/usr/lib/qt6/bin); the symlink keeps plain `qmlformat` invocations
# working for editors and quick iteration (the author's request).
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
    && python3 tools/capture_upload.py dist/screenshots"]
