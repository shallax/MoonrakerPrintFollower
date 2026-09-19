#!/bin/sh
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
