#!/usr/bin/env python3
"""Run a chosen list of test files, sharded across processes.

Domain files run in separate processes so each real-Qt file owns its
application. Shared fixture modules do not start an application at import.
The default is one process per file. Optional --shards distributes test ids
round-robin across more processes; use it only for order-independent modules.
The seek performance budget runs alone after the correctness worker pool.

Shards are only used when a module has more tests than chunks: sharding a
four-test file into four processes costs more in interpreter and engine
startup than it saves.

    tools/run_some.py tests.test_qml_plate_composition tests.test_monitor_qml_contracts
    tools/run_some.py --jobs 8 tests.test_qml_plate_composition

One log per shard under the scratch dir; a per-module verdict prints at the
end, naming the shard log to read when a module fails. Any failing shard
fails the run, and an empty module list is a usage error — never a pass.
"""
from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp/mpf"))
TIMED_MODULE = "tests.test_follower_seek_performance"

# `python3 -m unittest` puts the working directory on sys.path; importing
# the modules HERE to discover their tests must see the same layout, or a
# dotted module path resolves for the shards and not for discovery.
sys.path.insert(0, os.getcwd())


def _flatten(suite):
    """Every test id in a loaded suite, in discovery order."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item.id()


def discover(module_name):
    """The test ids a module holds, or None if it cannot be imported."""
    try:
        module = importlib.import_module(module_name)
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print("cannot import %s: %s" % (module_name, error), file=sys.stderr)
        return None
    return list(_flatten(unittest.defaultTestLoader.loadTestsFromModule(module)))


def shard(ids, count):
    """Deal ids round-robin, so a slow cluster spreads across workers."""
    if count <= 1 or len(ids) <= count:
        return [ids]
    chunks = [[] for _ in range(count)]
    for index, test_id in enumerate(ids):
        chunks[index % count].append(test_id)
    return [chunk for chunk in chunks if chunk]


def run_shard(job):
    """One process for one chunk. Returns (label, rc, log path, ran)."""
    label, ids, log_path = job
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        with open(log_path, "wb") as log:
            rc = subprocess.call(
                [sys.executable, "-m", "unittest", *ids],
                stdout=log, stderr=subprocess.STDOUT, env=environment,
            )
    except OSError as error:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("\ncannot run shard: %s\n" % error)
        rc = 1
    ran = 0
    try:
        for line in Path(log_path).read_text(
                encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Ran ") and " test" in line:
                ran = int(line.split()[1])
    except OSError:
        pass
    return label, rc, log_path, ran


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="+",
                        help="dotted test module paths")
    parser.add_argument("--jobs", type=int,
                        default=int(os.environ.get("JOBS", "8")),
                        help="processes to run at once (default 8)")
    parser.add_argument("--shards", type=int,
                        default=int(os.environ.get("SHARDS", "1")),
                        help="chunks per module (default 1 — see below)")
    args = parser.parse_args(argv)

    # The OS names this directory, so it cannot collide: the scratch
    # mount persists while a fresh container restarts PIDs from low
    # numbers, and a name derived from a CLOCK would be worse than
    # pid-named — wall time steps backwards, repeats within its own
    # resolution, and is quantised on Windows. A shard that dies
    # without writing must not leave the previous run's log to be read
    # as this run's verdict, so the name is unique by construction and
    # every log under it was written by this run.
    log_dir = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="run_some."))

    jobs = []
    timed_jobs = []
    expected = {}
    for module in args.modules:
        ids = discover(module)
        if ids is None:
            print("%-40s %s" % (module.rsplit(".", 1)[-1], "IMPORT FAILED"))
            return 1
        if not ids:
            print("no tests discovered in %s" % module, file=sys.stderr)
            return 2
        expected[module] = len(ids)
        chunks = shard(ids, 1 if module == TIMED_MODULE else args.shards)
        for index, chunk in enumerate(chunks):
            label = module if len(chunks) == 1 else "%s#%d" % (module, index + 1)
            target = timed_jobs if module == TIMED_MODULE else jobs
            target.append((label, chunk, log_dir / ("%s.log" % label.replace(".", "_"))))

    if not jobs and not timed_jobs:
        print("no tests discovered — refusing to report a green run",
              file=sys.stderr)
        return 2

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(run_shard, jobs))
    results.extend(run_shard(job) for job in timed_jobs)

    # Per-module verdict from its shards: the run count must add up to
    # what discovery found, or a shard silently ran nothing.
    verdicts = {}
    for label, rc, log_path, ran in results:
        module = label.split("#")[0]
        entry = verdicts.setdefault(module, {"rc": 0, "ran": 0, "logs": []})
        entry["rc"] = entry["rc"] or rc
        entry["ran"] += ran
        if rc != 0:
            entry["logs"].append(str(log_path))

    failed = 0
    for module in args.modules:
        name = module.rsplit(".", 1)[-1]
        entry = verdicts[module]
        short = expected[module] - entry["ran"]
        note = "" if not short else "  MISSING %d test(s)" % short
        print("%-46s %-6s %4d test(s)%s"
              % (name, "OK" if entry["rc"] == 0 else "FAIL",
                 entry["ran"], note))
        if entry["rc"] != 0:
            failed = 1
            for log_path in entry["logs"]:
                print("  log: %s" % log_path)
        elif short:
            failed = 1
            print("  discovery found %d, the shards ran %d"
                  % (expected[module], entry["ran"]))

    if not failed:
        print("all %d module(s) passed (%d shard(s), jobs=%d)"
              % (len(args.modules), len(jobs) + len(timed_jobs), args.jobs))
    return failed


if __name__ == "__main__":
    sys.exit(main())
