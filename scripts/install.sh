#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PREFIX=${PREFIX:-/usr/local}
LIB_DIR="$PREFIX/lib/sshman"
BIN_DIR="$PREFIX/bin"

mkdir -p "$LIB_DIR" "$BIN_DIR"
rm -rf "$LIB_DIR/sshman"
cp -R "$REPO_ROOT/sshman" "$LIB_DIR/sshman"

cat > "$BIN_DIR/sshman" <<EOF
#!/bin/sh
PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec /usr/bin/env python3 -m sshman.cli "\$@"
EOF

chmod +x "$BIN_DIR/sshman"

echo "Installed sshman to $BIN_DIR/sshman"
echo "Library files copied to $LIB_DIR"

