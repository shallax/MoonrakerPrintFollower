#!/bin/sh
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-10-01 [1d8bd42] Capitalise the remaining top-level directories
# 2026-09-08 [7341f82] Development tooling: pinned container, quality gates and the Makefile

# Install the git pre-commit hook that runs tools/pre_commit.sh.
# --git-path hooks resolves worktrees too: the .git entry is a pointer
# file there, not a directory (the 2026-09-18 find).
set -eu
root="$(git rev-parse --show-toplevel)"
hooks="$(git rev-parse --git-path hooks)"
chmod +x "$root/tools/pre_commit.sh"
cat > "$hooks/pre-commit" <<'HOOK'
#!/bin/sh
exec "$(git rev-parse --show-toplevel)/tools/pre_commit.sh"
HOOK
chmod +x "$hooks/pre-commit"
echo "Installed pre-commit hook ($hooks/pre-commit). Remove it to disable."
