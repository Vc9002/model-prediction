#!/bin/bash
# Install the dashboard as a macOS launchd service: starts at login,
# restarts automatically if it crashes. Run once:  bash install_dashboard_service.sh
# Uninstall:  launchctl unload ~/Library/LaunchAgents/com.vc.model-dashboard.plist
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.vc.model-dashboard.plist"
PYTHON="$(command -v python3)"
mkdir -p "$PROJECT_DIR/dashboard" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.vc.model-dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${PROJECT_DIR}/dashboard_server.py</string>
  </array>
  <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${PROJECT_DIR}/dashboard/launchd.out.log</string>
  <key>StandardErrorPath</key><string>${PROJECT_DIR}/dashboard/launchd.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 2
if curl -s --max-time 4 http://127.0.0.1:8765/api/health >/dev/null; then
  echo "✔ dashboard service installed and running: http://127.0.0.1:8765/"
  echo "  it now starts at login and restarts itself if it ever crashes."
else
  echo "✖ service loaded but the health check failed — see:"
  echo "  ${PROJECT_DIR}/dashboard/launchd.err.log"
  exit 1
fi
