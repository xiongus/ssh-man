#!/bin/sh
set -eu

PREFIX=${PREFIX:-/usr/local}
LIB_DIR="$PREFIX/lib/sshman"
BIN_PATH="$PREFIX/bin/sshman"

rm -f "$BIN_PATH"
rm -rf "$LIB_DIR"

echo "Removed $BIN_PATH"
echo "Removed $LIB_DIR"
