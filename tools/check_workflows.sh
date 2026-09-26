#!/bin/sh
# Run the pinned workflow linter natively inside the dev container.
set -eu
case "$(uname -m)" in
    aarch64|arm64) architecture=arm64 ;;
    x86_64) architecture=amd64 ;;
    *) echo "unsupported actionlint architecture" >&2; exit 1 ;;
esac
mkdir -p /tmp/mpf
scratch="$(mktemp -d /tmp/mpf/actionlint.XXXXXX)"
trap 'rm -rf "$scratch"' EXIT
wget -qO "$scratch/archive.tar.gz" "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_${architecture}.tar.gz"
tar -xzf "$scratch/archive.tar.gz" -C "$scratch" actionlint
"$scratch/actionlint" .github/workflows/*.yml
