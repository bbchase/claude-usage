#!/bin/sh
# Install claude-usage: PATH symlink + launchd agent that refreshes the
# Cache every 5 minutes. Safe to re-run (idempotent).
set -eu

REPO="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.claude-usage"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN_DIR="${CLAUDE_USAGE_BIN_DIR:-$HOME/.local/bin}"

chmod +x "$REPO/bin/claude-usage"
mkdir -p "$BIN_DIR" "$HOME/.cache/claude-usage" "$HOME/Library/LaunchAgents"
ln -sf "$REPO/bin/claude-usage" "$BIN_DIR/claude-usage"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$REPO/claude_usage.py</string>
    <string>--fetch</string>
  </array>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/.cache/claude-usage/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.cache/claude-usage/launchd.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

cat <<EOF
Installed:
  $BIN_DIR/claude-usage -> $REPO/bin/claude-usage
  $PLIST (refreshes every 5 min)

Make sure $BIN_DIR is on your PATH, then run: claude-usage

Optional Claude Code statusline — add to ~/.claude/settings.json:
  "statusLine": {
    "type": "command",
    "command": "/usr/bin/python3 $REPO/claude_usage.py --statusline"
  }

The first fetch reads your Claude Code OAuth token from the macOS Keychain;
approve the Keychain prompt if one appears.
EOF
