#!/bin/sh
# Install the git pre-commit hook that runs tools/pre_commit.sh.
set -eu
root="$(git rev-parse --show-toplevel)"
chmod +x "$root/tools/pre_commit.sh"
cat > "$root/.git/hooks/pre-commit" <<'HOOK'
#!/bin/sh
exec "$(git rev-parse --show-toplevel)/tools/pre_commit.sh"
HOOK
chmod +x "$root/.git/hooks/pre-commit"
echo "Installed pre-commit hook ($root/.git/hooks/pre-commit). Remove it to disable."
