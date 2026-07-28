#!/usr/bin/env bash
# Apply the Linux case-sensitivity symlink fixes described in docs/LINUX_FIXES.md.
# Usage: scripts/apply-linux-fixes.sh [terminal_dir]
#   terminal_dir defaults to "$HOME/.mt5/drive_c/Program Files/MetaTrader 5"
set -euo pipefail

TERMINAL_DIR="${1:-$HOME/.mt5/drive_c/Program Files/MetaTrader 5}"

if [[ ! -d "$TERMINAL_DIR" ]]; then
    echo "error: terminal dir not found: $TERMINAL_DIR" >&2
    exit 1
fi

link() {
    local target=$1 name=$2
    if [[ -e "$TERMINAL_DIR/$name" || -L "$TERMINAL_DIR/$name" ]]; then
        echo "ok: $name already exists"
    elif [[ -e "$TERMINAL_DIR/$target" ]]; then
        ln -s "$target" "$TERMINAL_DIR/$name"
        echo "created: $name -> $target"
    else
        echo "skip: $target not found in $TERMINAL_DIR" >&2
    fi
}

link Config config
link MetaEditor64.exe metaeditor64.exe
