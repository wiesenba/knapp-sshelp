#!/usr/bin/env bash
# User-level Linux installation; never changes Windows/WSL networking.
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
deps=0
args=()
for arg in "$@"; do
    case "$arg" in
        --install-deps) deps=1 ;;
        --no-setup|--no-shell|--isolated) args+=("$arg") ;;
        -h|--help)
            printf 'Usage: bash install.sh [--install-deps] [--no-setup] [--no-shell] [--isolated]\n'
            printf 'Dependencies: Python 3.10+, OpenSSH, fzf 0.44+. Ubuntu 24.04+ recommended.\n'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
    esac
done
[[ $(uname -s) == Linux ]] || { printf 'Open Ubuntu/WSL and run this installer there.\n' >&2; exit 1; }
[[ $EUID -ne 0 ]] || { printf 'Run as your normal Linux user, not root or sudo.\n' >&2; exit 1; }
missing=0
for command in python3 ssh fzf; do
    command -v "$command" >/dev/null 2>&1 || missing=1
done
if [[ $missing == 1 && $deps == 0 && -t 0 ]]; then
    read -r -p 'Install missing dependencies using sudo apt-get? [y/N] ' answer
    [[ $answer == y || $answer == Y ]] && deps=1
fi
if [[ $deps == 1 ]]; then
    command -v apt-get >/dev/null || { printf 'Install dependencies with your distribution package manager.\n' >&2; exit 1; }
    sudo apt-get update
    sudo apt-get install -y python3 openssh-client fzf iputils-ping
fi
for command in python3 ssh fzf; do
    command -v "$command" >/dev/null 2>&1 || { printf 'Missing %s. Re-run with --install-deps on Ubuntu 24.04+.\n' "$command" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ is required.")'
python3 "$root/tools/install.py" "${args[@]}"