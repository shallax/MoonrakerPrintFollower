#!/usr/bin/env python3
"""Run the shared Makefile procedures in the pinned Linux image from macOS/Windows."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "moonraker-print-follower-dev"


def platform_for(command):
    # Pixel baselines are x86_64. Everything else can use the host CPU,
    # avoiding slow and unstable QEMU runs on Apple Silicon/Windows ARM.
    if command == "refresh":
        return "linux/amd64"
    return os.environ.get("DOCKER_DEFAULT_PLATFORM") or (
        "linux/arm64" if platform.machine().lower() in ("arm64", "aarch64")
        else "linux/amd64")


def image_for(docker_platform):
    return IMAGE + ":" + docker_platform.rsplit("/", 1)[-1]


def docker_env(docker_platform):
    env = dict(os.environ)
    env["DOCKER_DEFAULT_PLATFORM"] = docker_platform
    return env


def run(argv, env=None):
    print("$ " + " ".join(str(arg) for arg in argv), flush=True)
    try:
        return subprocess.run([str(arg) for arg in argv], cwd=ROOT, env=env,
                              stdin=subprocess.DEVNULL).returncode
    except OSError as error:
        print("cannot run %s: %s" % (argv[0], error), file=sys.stderr)
        return 1


def build(docker_platform, pull=False):
    argv = ["docker", "build", "--platform", docker_platform,
            "-t", image_for(docker_platform)]
    if pull:
        argv.append("--pull")
    return run([*argv, "."], docker_env(docker_platform))


def inside(command, docker_platform):
    argv = ["docker", "run", "--rm", "--platform", docker_platform,
            "-v", "%s:/work" % ROOT, "-w", "/work"]
    if (ROOT / ".git").is_file():
        for flag in ("--git-dir", "--git-common-dir"):
            result = subprocess.run(["git", "rev-parse", flag], cwd=ROOT,
                                    text=True, capture_output=True, check=True)
            path = (ROOT / result.stdout.strip()).resolve()
            argv += ["-v", "%s:%s" % (path, path)]
    if hasattr(os, "getuid"):
        argv += ["--user", "%s:%s" % (os.getuid(), os.getgid())]
    for key in ("JOBS", "MPF_SH", "COVERAGE", "SOURCE_DATE_EPOCH"):
        if key in os.environ:
            argv += ["-e", "%s=%s" % (key, os.environ[key])]
    argv += [image_for(docker_platform), *command]
    return run(argv, docker_env(docker_platform))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command")
    parser.add_argument("options", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not shutil.which("docker"):
        parser.error("Docker is unavailable; install Docker Desktop or use BACKEND=native")
    if args.command in ("ui_test", "ui_release_gate"):
        # The build artifacts can come from Docker, but Cura itself runs
        # through the desktop host harness on macOS and Windows.
        return run([sys.executable, "tools/native/dev.py", args.command, *args.options])
    docker_platform = platform_for(args.command)
    if args.command == "dev_down":
        return run(["docker", "rm", "-f", "mpf-dev"], docker_env(docker_platform))
    if build(docker_platform, pull=args.command == "refresh"):
        return 1
    if args.command == "bootstrap":
        return 0
    if args.command == "hooks":
        result = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                                cwd=ROOT, text=True, capture_output=True, check=True)
        hook = (ROOT / result.stdout.strip()).resolve() / "pre-commit"
        python = sys.executable.replace("\\", "/")
        driver = str(ROOT / "tools" / "native" / "docker.py").replace("\\", "/")
        hook.write_text('#!/bin/sh\nexec "%s" "%s" hook-check\n' % (python, driver),
                        encoding="utf-8", newline="\n")
        if os.name != "nt":
            hook.chmod(0o755)
        print("installed %s" % hook)
        return 0
    if args.command == "dev_up":
        return run(["docker", "run", "-d", "--platform", docker_platform,
                    "--name", "mpf-dev", "-v", "%s:/work" % ROOT,
                    image_for(docker_platform), "sleep", "infinity"],
                   docker_env(docker_platform))
    if args.command == "refresh":
        for old in (ROOT / "dist" / "screenshots").glob("*.png"):
            old.unlink()
    if args.command == "snapshot":
        status = inside(["python3", "tools/native/dev.py", "package"], docker_platform)
        if status:
            return status
        version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["package_version"]
        source = ROOT / "dist" / ("MoonrakerPrintFollower-v%s.curapackage" % version)
        target = Path(tempfile.gettempdir()) / "mpf.curapackage"
        shutil.copyfile(source, target)
        print("wrote %s" % target)
        return 0
    command = ["python3", "tools/native/dev.py", args.command, *args.options]
    if args.command == "refresh":
        # The committed pixels use the Linux capture script's CPU feature
        # mask; it is part of the canonical screenshot contract.
        command = ["sh", "tools/run_captures.sh", "dist/screenshots"]
    if args.command == "exec":
        command = args.options
        if not command:
            parser.error("exec needs a command")
    status = inside(command, docker_platform)
    if status == 0 and args.command == "refresh":
        for source in (ROOT / "dist" / "screenshots").glob("*.png"):
            shutil.copyfile(source, ROOT / "screenshots" / source.name)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
