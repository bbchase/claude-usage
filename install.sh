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

SETTINGS="${CLAUDE_USAGE_SETTINGS:-$HOME/.claude/settings.json}"
STATUS_CMD="/usr/bin/python3 $REPO/claude_usage.py --statusline"

# Add the Claude Code statusLine entry. Exit codes from the helper:
# 0 = written, 10 = already set to ours, 11 = a different statusLine exists
# (only replaced when $1 is "force"), 12 = settings file isn't valid JSON.
set_statusline() {
  /usr/bin/python3 - "$SETTINGS" "$STATUS_CMD" "$1" <<'PY'
import json, os, shutil, sys

path, cmd, force = sys.argv[1], sys.argv[2], sys.argv[3] == "force"
entry = {"type": "command", "command": cmd}
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}
except ValueError:
    sys.exit(12)
if not isinstance(data, dict):
    sys.exit(12)
current = data.get("statusLine")
if current == entry:
    sys.exit(10)
if current is not None and not force:
    sys.exit(11)
if os.path.exists(path):
    shutil.copy2(path, path + ".bak")
data["statusLine"] = entry
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp, path)
PY
}

rc=0
set_statusline no || rc=$?
if [ "$rc" -eq 11 ]; then
  printf 'A different statusLine is already set in %s.\nReplace it (a .bak copy is kept)? [y/N] ' "$SETTINGS"
  answer=""
  if [ -t 0 ]; then read -r answer || answer=""; else echo; fi
  case "$answer" in
    y|Y|yes|YES) rc=0; set_statusline force || rc=$? ;;
  esac
fi
case "$rc" in
  0)  STATUS_NOTE="Statusline: set in $SETTINGS"
      [ -f "$SETTINGS.bak" ] && STATUS_NOTE="$STATUS_NOTE (backup: $SETTINGS.bak)" ;;
  10) STATUS_NOTE="Statusline: already configured in $SETTINGS" ;;
  11) STATUS_NOTE="Statusline: left your existing statusLine unchanged" ;;
  *)  STATUS_NOTE="Statusline: could not edit $SETTINGS (is it valid JSON?)
To enable it manually, add:
  \"statusLine\": {\"type\": \"command\", \"command\": \"$STATUS_CMD\"}" ;;
esac

cat <<EOF
Installed:
  $BIN_DIR/claude-usage -> $REPO/bin/claude-usage
  $PLIST (refreshes every 5 min)
  $STATUS_NOTE

Make sure $BIN_DIR is on your PATH, then run: claude-usage

The first fetch reads your Claude Code OAuth token from the macOS Keychain;
approve the Keychain prompt if one appears.
EOF
